import asyncio
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from starlette.testclient import TestClient

from app.core.db import Base, make_engine, make_session_factory
from app.domain.results import Finding, FindingStatus, InspectionResult, Verdict
from app.domain.states import ImageStatus, TaskStatus
from app.domain.usage import AttemptUsageTrace
from app.main import create_app
from app.models import InferenceAttempt, InspectionTask, TaskImage
from app.repositories import TaskRepository
from app.services.ark import (
    ArkRequestError,
    ArkRuntimeConfig,
    InspectionOutcome,
    MalformedInspectionResult,
)
from app.services.events import TaskEventBroker
from app.services.runner import TaskRunner
from tests.usage_fixtures import failed_usage, successful_usage


def passing_result(marker: str) -> InspectionResult:
    return InspectionResult(
        image_valid=True,
        verdict=Verdict.PASS,
        summary=marker,
        findings=[
            Finding(
                rule_id="uniform",
                rule_name="uniform",
                status=FindingStatus.PASS,
                observation="clean",
                visual_evidence=marker,
            )
        ],
    )


class FakeInspector:
    def __init__(
        self,
        delay: float = 0,
        error_status: int | None = None,
        rate_limit_failures: int = 0,
        malformed: bool = False,
    ) -> None:
        self.delay = delay
        self.error_status = error_status
        self.rate_limit_failures = rate_limit_failures
        self.malformed = malformed
        self.active = 0
        self.max_active = 0
        self.call_count = 0
        self._lock = threading.Lock()

    def inspect(
        self,
        config: ArkRuntimeConfig,
        system_prompt: str,
        user_prompt: str,
        image_path: Path,
    ) -> InspectionOutcome:
        del system_prompt, user_prompt
        with self._lock:
            self.active += 1
            self.call_count += 1
            call_number = self.call_count
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.error_status is not None:
                raise ArkRequestError(
                    self.error_status,
                    {},
                    "fake_error",
                    usage=failed_usage(config.protocol),
                )
            if call_number <= self.rate_limit_failures:
                raise ArkRequestError(
                    429,
                    {"retry-after": "0.001"},
                    "rate_limit",
                    usage=failed_usage(config.protocol),
                )
            if self.malformed:
                raise MalformedInspectionResult(
                    "bad model output",
                    usage=successful_usage(config.protocol),
                )
            return InspectionOutcome(
                result=passing_result(image_path.name),
                usage=successful_usage(config.protocol),
            )
        finally:
            with self._lock:
                self.active -= 1


@dataclass
class TaskFixture:
    sessions: object
    task_id: str
    engine: Engine

    def count_images(self, status: str) -> int:
        with self.sessions() as session:  # type: ignore[operator]
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(TaskImage)
                    .where(TaskImage.task_id == self.task_id, TaskImage.status == status)
                )
                or 0
            )

    def task_status(self) -> str:
        with self.sessions() as session:  # type: ignore[operator]
            task = session.get(InspectionTask, self.task_id)
            assert task is not None
            return task.status

    def attempt_count(self) -> int:
        with self.sessions() as session:  # type: ignore[operator]
            return int(session.scalar(select(func.count()).select_from(InferenceAttempt)) or 0)

    def attempts(self) -> list[InferenceAttempt]:
        with self.sessions() as session:  # type: ignore[operator]
            return list(
                session.scalars(
                    select(InferenceAttempt).order_by(InferenceAttempt.created_at)
                )
            )

    def image_attempt_count(self) -> int:
        with self.sessions() as session:  # type: ignore[operator]
            attempt_count = session.scalar(
                select(TaskImage.attempt_count).where(TaskImage.task_id == self.task_id)
            )
            assert attempt_count is not None
            return attempt_count


def make_task_fixture(
    tmp_path: Path,
    image_count: int = 8,
    configured_concurrency: int | None = None,
) -> TaskFixture:
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = make_engine(tmp_path / f"runner-{image_count}.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions() as session:
        repo = TaskRepository(session)
        model_snapshot = {
            "base_url": "https://ark.example/v3",
            "model": "seed",
            "protocol": "responses",
            "thinking": "disabled",
            "timeout_seconds": 120,
            "max_retries": 2,
        }
        if configured_concurrency is not None:
            model_snapshot["concurrency"] = configured_concurrency
        task = repo.create_task(
            "runner test",
            {
                "system_prompt": "visible evidence only",
                "task_prompt": "inspect",
                "rules": [
                    {"rule_id": "uniform", "name": "uniform", "text": "check uniform"}
                ],
            },
            model_snapshot,
        )
        for position in range(image_count):
            image_path = tmp_path / f"{position}.jpg"
            image_path.write_bytes(f"image-{position}".encode())
            repo.add_image(
                task.id,
                position,
                image_path.name,
                f"hash-{position}",
                str(image_path),
                str(image_path),
                str(image_path),
            )
        repo.set_task_status(task.id, TaskStatus.QUEUED)
        session.commit()
    return TaskFixture(sessions=sessions, task_id=task.id, engine=engine)


@pytest.mark.asyncio
async def test_runner_never_exceeds_configured_concurrency(tmp_path) -> None:
    for run in range(5):
        fixture = make_task_fixture(tmp_path / str(run))
        inspector = FakeInspector(delay=0.02)
        runner = TaskRunner(fixture.sessions, inspector, concurrency=3)

        await runner.run_task(fixture.task_id)

        assert inspector.max_active == 3
        assert fixture.count_images("succeeded") == 8
        assert fixture.task_status() == TaskStatus.REVIEW_REQUIRED


@pytest.mark.asyncio
async def test_runner_uses_task_snapshot_concurrency(tmp_path) -> None:
    fixture = make_task_fixture(tmp_path, image_count=6, configured_concurrency=2)
    inspector = FakeInspector(delay=0.02)
    runner = TaskRunner(fixture.sessions, inspector, concurrency=5)

    await runner.run_task(fixture.task_id)

    assert inspector.max_active == 2


@pytest.mark.asyncio
async def test_runner_persists_versioned_usage_trace_for_success(tmp_path) -> None:
    fixture = make_task_fixture(tmp_path, image_count=1)
    runner = TaskRunner(fixture.sessions, FakeInspector(), concurrency=1)

    await runner.run_task(fixture.task_id)

    attempts = fixture.attempts()
    assert len(attempts) == 1
    trace = AttemptUsageTrace.model_validate(attempts[0].usage_json)
    assert trace.schema_version == 1
    assert trace.totals.model_dump() == {
        "known_call_count": 1,
        "unknown_call_count": 0,
        "input_tokens": 10,
        "cached_input_tokens": 4,
        "output_tokens": 2,
        "reasoning_tokens": 0,
        "total_tokens": 12,
    }


@pytest.mark.asyncio
async def test_runner_pauses_task_on_auth_error(tmp_path) -> None:
    fixture = make_task_fixture(tmp_path)
    runner = TaskRunner(fixture.sessions, FakeInspector(error_status=401), concurrency=3)

    await runner.run_task(fixture.task_id)

    assert fixture.task_status() == TaskStatus.PAUSED
    assert fixture.count_images(ImageStatus.FAILED) >= 1


@pytest.mark.asyncio
async def test_runner_retries_429_twice_then_succeeds(tmp_path) -> None:
    fixture = make_task_fixture(tmp_path, image_count=1)
    inspector = FakeInspector(rate_limit_failures=2)
    runner = TaskRunner(fixture.sessions, inspector, concurrency=1, sleep=lambda _delay: None)

    await runner.run_task(fixture.task_id)

    assert inspector.call_count == 3
    assert fixture.attempt_count() == 3
    assert [attempt.attempt_number for attempt in fixture.attempts()] == [1, 2, 3]
    assert fixture.image_attempt_count() == 3
    assert fixture.count_images(ImageStatus.SUCCEEDED) == 1


@pytest.mark.asyncio
async def test_runner_preserves_failed_and_successful_retry_usage(tmp_path) -> None:
    fixture = make_task_fixture(tmp_path, image_count=1)
    inspector = FakeInspector(rate_limit_failures=1)
    runner = TaskRunner(fixture.sessions, inspector, concurrency=1, sleep=lambda _delay: None)

    await runner.run_task(fixture.task_id)

    attempts = fixture.attempts()
    assert [attempt.status for attempt in attempts] == ["failed", "succeeded"]
    failed_trace = AttemptUsageTrace.model_validate(attempts[0].usage_json)
    successful_trace = AttemptUsageTrace.model_validate(attempts[1].usage_json)
    assert failed_trace.totals.known_call_count == 0
    assert failed_trace.totals.unknown_call_count == 1
    assert successful_trace.totals.known_call_count == 1
    assert successful_trace.totals.unknown_call_count == 0
    assert successful_trace.totals.total_tokens == 12


@pytest.mark.asyncio
async def test_malformed_result_becomes_failed_and_requires_review(tmp_path) -> None:
    fixture = make_task_fixture(tmp_path, image_count=1)
    runner = TaskRunner(fixture.sessions, FakeInspector(malformed=True), concurrency=1)

    await runner.run_task(fixture.task_id)

    assert fixture.count_images(ImageStatus.FAILED) == 1
    assert fixture.task_status() == TaskStatus.REVIEW_REQUIRED
    attempts = fixture.attempts()
    assert len(attempts) == 1
    trace = AttemptUsageTrace.model_validate(attempts[0].usage_json)
    assert trace.totals.known_call_count == 1
    assert trace.totals.total_tokens == 12


@pytest.mark.asyncio
async def test_successful_task_waits_for_report_when_auto_export_is_unavailable(
    tmp_path,
) -> None:
    fixture = make_task_fixture(tmp_path, image_count=1)

    async def unavailable(_task_id: str) -> None:
        return None

    runner = TaskRunner(
        fixture.sessions,
        FakeInspector(),
        concurrency=1,
        report_callback=unavailable,
    )

    await runner.run_task(fixture.task_id)

    assert fixture.task_status() == TaskStatus.REVIEW_REQUIRED


@pytest.mark.asyncio
async def test_successful_task_enters_exporting_only_after_export_is_prepared(
    tmp_path,
) -> None:
    fixture = make_task_fixture(tmp_path, image_count=1)

    async def prepared(task_id: str) -> str:
        with fixture.sessions() as session:  # type: ignore[operator]
            TaskRepository(session).set_task_status(task_id, TaskStatus.EXPORTING)
            session.commit()
        return "export-1"

    runner = TaskRunner(
        fixture.sessions,
        FakeInspector(),
        concurrency=1,
        report_callback=prepared,
    )

    await runner.run_task(fixture.task_id)

    assert fixture.task_status() == TaskStatus.EXPORTING


@pytest.mark.asyncio
async def test_event_broker_drops_oldest_when_subscriber_is_slow() -> None:
    broker = TaskEventBroker(queue_size=2)

    async with broker.subscribe("task-1") as queue:
        await broker.publish("task-1", {"type": "one"})
        await broker.publish("task-1", {"type": "two"})
        await broker.publish("task-1", {"type": "three"})
        assert await asyncio.wait_for(queue.get(), 0.1) == {"type": "two"}
        assert await asyncio.wait_for(queue.get(), 0.1) == {"type": "three"}


@pytest.mark.asyncio
async def test_event_broker_replays_events_after_last_seen_id() -> None:
    broker = TaskEventBroker(queue_size=4)
    await broker.publish("task-1", {"type": "one"})
    await broker.publish("task-1", {"type": "two"})
    await broker.publish("task-1", {"type": "three"})

    async with broker.subscribe("task-1", after_id="1") as queue:
        second = await asyncio.wait_for(queue.get(), 0.1)
        third = await asyncio.wait_for(queue.get(), 0.1)

    assert second == {"type": "two"}
    assert third == {"type": "three"}
    assert second.event_id == "2"
    assert third.event_id == "3"


def test_application_startup_recovers_without_resuming_calls(tmp_path) -> None:
    fixture = make_task_fixture(tmp_path, image_count=1)
    with fixture.sessions() as session:  # type: ignore[operator]
        repo = TaskRepository(session)
        repo.set_task_status(fixture.task_id, TaskStatus.RUNNING)
        image = repo.claim_next_image(fixture.task_id)
        assert image is not None
        session.commit()
    inspector = FakeInspector()

    with TestClient(create_app(engine=fixture.engine, inspection_service=inspector)):
        pass

    assert fixture.task_status() == TaskStatus.PAUSED
    assert fixture.count_images(ImageStatus.QUEUED) == 1
    assert inspector.call_count == 0
