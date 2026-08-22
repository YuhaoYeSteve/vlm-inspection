import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from starlette.testclient import TestClient

from app.core.config import Settings
from app.core.db import Base, make_engine
from app.domain.results import Finding, FindingStatus, InspectionResult, Verdict
from app.main import create_app
from app.services.ark import ArkRuntimeConfig


@dataclass
class FakeSecretStore:
    values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


@dataclass
class AppHarness:
    client: TestClient
    database_path: Path
    secrets: FakeSecretStore
    inspector: "FakeInspectionService"

    @property
    def seeded_template_id(self) -> str:
        response = self.client.get("/api/templates")
        assert response.status_code == 200
        return str(response.json()[0]["id"])


@dataclass
class FakeInspectionService:
    call_count: int = 0
    delay: float = 0
    error_status: int | None = None

    def inspect(
        self,
        config: ArkRuntimeConfig,
        system_prompt: str,
        user_prompt: str,
        image_path: Path,
    ) -> InspectionResult:
        self.call_count += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error_status is not None:
            from app.services.ark import ArkRequestError

            raise ArkRequestError(self.error_status, {}, "fake_error")
        return InspectionResult(
            image_valid=True,
            verdict=Verdict.PASS,
            summary="test image is readable",
            overall_confidence=1,
            findings=[
                Finding(
                    rule_id="readable",
                    rule_name="readable",
                    status=FindingStatus.PASS,
                    observation="image decoded",
                    visual_evidence=image_path.name,
                    confidence=1,
                )
            ],
        )


@pytest.fixture
def app_harness(tmp_path: Path) -> Iterator[AppHarness]:
    database_path = tmp_path / "app.db"
    engine = make_engine(database_path)
    Base.metadata.create_all(engine)
    secrets = FakeSecretStore()
    inspector = FakeInspectionService()
    with TestClient(
        create_app(
            settings=Settings(data_dir=tmp_path / "data"),
            engine=engine,
            secret_store=secrets,
            inspection_service=inspector,
        )
    ) as client:
        yield AppHarness(
            client=client,
            database_path=database_path,
            secrets=secrets,
            inspector=inspector,
        )


@pytest.fixture
def png_file() -> bytes:
    output = BytesIO()
    Image.new("RGB", (64, 64), "white").save(output, format="PNG")
    return output.getvalue()
