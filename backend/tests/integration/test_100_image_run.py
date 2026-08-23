import asyncio
import hashlib
import threading
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import Engine, func, select
from starlette.testclient import TestClient

from app.core.config import Settings
from app.core.db import Base, make_engine, make_session_factory
from app.domain.results import Finding, FindingStatus, InspectionResult, Verdict
from app.domain.states import TaskStatus
from app.main import create_app
from app.models import InferenceResultRecord, InspectionTask, TaskImage
from app.repositories import TaskRepository
from app.services.ark import ArkRuntimeConfig, InspectionOutcome
from app.services.runner import TaskRunner
from tests.usage_fixtures import successful_usage


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class MarkerInspector:
    def inspect(
        self,
        config: ArkRuntimeConfig,
        system_prompt: str,
        user_prompt: str,
        image_path: Path,
    ) -> InspectionOutcome:
        del system_prompt, user_prompt
        marker = hashlib.sha256(image_path.read_bytes()).hexdigest()
        return InspectionOutcome(
            result=InspectionResult(
                image_valid=True,
                verdict=Verdict.PASS,
                summary=marker,
                findings=[
                    Finding(
                        rule_id="binding",
                        rule_name="result binding",
                        status=FindingStatus.PASS,
                        observation=marker,
                        visual_evidence=marker,
                    )
                ],
            ),
            usage=successful_usage(config.protocol),
        )


class GatedInspector(MarkerInspector):
    def __init__(self, successful_calls: int) -> None:
        self.successful_calls = successful_calls
        self.calls = 0
        self.blocked = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()

    def inspect(
        self,
        config: ArkRuntimeConfig,
        system_prompt: str,
        user_prompt: str,
        image_path: Path,
    ) -> InspectionOutcome:
        with self._lock:
            self.calls += 1
            call_number = self.calls
        if call_number > self.successful_calls:
            self.blocked.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError("test gate timed out")
        return super().inspect(config, system_prompt, user_prompt, image_path)


def seed_100_image_task(tmp_path: Path) -> tuple[Engine, object, str, Settings]:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_directories()
    engine = make_engine(settings.database_path)
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    image_dir = settings.image_dir / "restart-test"
    image_dir.mkdir(parents=True)

    with sessions() as session:
        repo = TaskRepository(session)
        task = repo.create_task(
            "100 image restart",
            {
                "template_name": "restart contract",
                "template_version": 1,
                "system_prompt": "visible evidence only",
                "task_prompt": "return the image marker",
                "rules": [{"rule_id": "binding", "name": "binding", "text": "bind"}],
                "model_params": {"temperature": 0, "max_output_tokens": 512},
            },
            {
                "base_url": "https://ark.example/v3",
                "model": "seed-test",
                "protocol": "responses",
                "thinking": "disabled",
                "timeout_seconds": 120,
                "max_retries": 0,
                "concurrency": 3,
            },
        )
        for position in range(100):
            output = BytesIO()
            Image.new(
                "RGB",
                (32, 32),
                (position, position * 17 % 256, position * 37 % 256),
            ).save(output, format="PNG")
            content = output.getvalue()
            path = image_dir / f"{position:03}.png"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            repo.add_image(
                task.id,
                position,
                path.name,
                digest,
                str(path),
                str(path),
                str(path),
            )
        repo.set_task_status(task.id, TaskStatus.QUEUED)
        session.commit()
        task_id = task.id
    return engine, sessions, task_id, settings


def succeeded_count(engine: Engine, task_id: str) -> int:
    sessions = make_session_factory(engine)
    with sessions() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(TaskImage)
                .where(TaskImage.task_id == task_id, TaskImage.status == "succeeded")
            )
            or 0
        )


@pytest.mark.asyncio
async def test_100_images_resume_without_result_misalignment(tmp_path: Path) -> None:
    engine, sessions, task_id, settings = seed_100_image_task(tmp_path)
    interrupted_inspector = GatedInspector(successful_calls=40)
    first_runner = TaskRunner(sessions, interrupted_inspector, concurrency=3)
    running = asyncio.create_task(first_runner.run_task(task_id))

    deadline = asyncio.get_running_loop().time() + 5
    while succeeded_count(engine, task_id) < 40 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert succeeded_count(engine, task_id) == 40
    assert interrupted_inspector.blocked.wait(timeout=1)

    running.cancel()
    interrupted_inspector.release.set()
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.sleep(0.05)

    secrets = MemorySecretStore()
    with TestClient(
        create_app(
            settings=settings,
            engine=engine,
            secret_store=secrets,
            inspection_service=MarkerInspector(),
        )
    ) as client:
        recovered = client.get(f"/api/tasks/{task_id}").json()
        assert recovered["status"] == "paused"
        assert client.post(f"/api/tasks/{task_id}/resume").status_code == 202

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            detail = client.get(f"/api/tasks/{task_id}").json()
            if detail["status"] == "review_required":
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("resumed task did not finish")

    with sessions() as session:
        task = session.get(InspectionTask, task_id)
        assert task is not None
        assert task.status == "review_required"
        rows = session.execute(
            select(TaskImage, InferenceResultRecord)
            .join(InferenceResultRecord, InferenceResultRecord.image_id == TaskImage.id)
            .where(TaskImage.task_id == task_id, InferenceResultRecord.is_current.is_(True))
            .order_by(TaskImage.position)
        ).all()

    assert len(rows) == 100
    assert len({image.id for image, _result in rows}) == 100
    assert all(result.result_json["summary"] == image.sha256 for image, result in rows)
