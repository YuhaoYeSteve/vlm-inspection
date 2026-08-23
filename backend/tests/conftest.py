import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from app.services.feishu_client import BlockRef, DocumentRef
from app.services.lark_cli import FeishuConnection


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
    feishu_connection: "FakeFeishuConnectionProvider"
    feishu_client: "FakeReportFeishuClient"

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
    malformed: bool = False

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
        if self.malformed:
            from app.services.ark import MalformedInspectionResult

            raise MalformedInspectionResult("fake malformed output")
        return InspectionResult(
            image_valid=True,
            verdict=Verdict.PASS,
            summary="test image is readable",
            findings=[
                Finding(
                    rule_id="readable",
                    rule_name="readable",
                    status=FindingStatus.PASS,
                    observation="image decoded",
                    visual_evidence=image_path.name,
                )
            ],
        )


@dataclass
class FakeFeishuConnectionProvider:
    connection: FeishuConnection = field(
        default_factory=lambda: FeishuConnection(
            configured=True,
            connected=True,
            user_name="测试用户",
            app_name="示例应用",
            identity="user",
            token_status="valid",
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
            destination_path="测试报告目录",
            reference_url="https://example.feishu.cn/docx/reference",
        )
    )

    async def status(self) -> FeishuConnection:
        return self.connection


@dataclass
class FakeReportFeishuClient:
    created_documents: int = 0
    calls: list[str] = field(default_factory=list)

    async def create_document(
        self,
        title: str,
        folder_token: str,
        client_token: str,
    ) -> DocumentRef:
        del title, folder_token, client_token
        self.created_documents += 1
        document_id = f"report-{self.created_documents}"
        self.calls.append("create_document")
        return DocumentRef(document_id, f"https://example.feishu.cn/docx/{document_id}")

    async def create_blocks(
        self,
        document_id: str,
        parent_block_id: str,
        blocks: list[dict],
        operation_key: str,
    ) -> list[BlockRef]:
        del document_id, parent_block_id, blocks
        self.calls.append(operation_key)
        return [BlockRef(f"block-{len(self.calls)}")]

    async def create_image_block(
        self,
        document_id: str,
        parent_block_id: str,
        operation_key: str,
    ) -> BlockRef:
        del document_id, parent_block_id
        self.calls.append(operation_key)
        return BlockRef(f"image-{len(self.calls)}")

    async def upload_docx_image(self, block_id: str, filename: str, content: bytes) -> str:
        del block_id, filename, content
        self.calls.append("upload")
        return f"file-{len(self.calls)}"


@pytest.fixture
def app_harness(tmp_path: Path) -> Iterator[AppHarness]:
    database_path = tmp_path / "app.db"
    engine = make_engine(database_path)
    Base.metadata.create_all(engine)
    secrets = FakeSecretStore()
    inspector = FakeInspectionService()
    feishu_connection = FakeFeishuConnectionProvider()
    feishu_client = FakeReportFeishuClient()
    with TestClient(
        create_app(
            settings=Settings(
                data_dir=tmp_path / "data",
                feishu_reference_url="https://example.feishu.cn/docx/reference",
                feishu_parent_token="folder-test",
                feishu_parent_label="测试报告目录",
            ),
            engine=engine,
            secret_store=secrets,
            inspection_service=inspector,
            feishu_connection_provider=feishu_connection,
            feishu_client=feishu_client,
        )
    ) as client:
        yield AppHarness(
            client=client,
            database_path=database_path,
            secrets=secrets,
            inspector=inspector,
            feishu_connection=feishu_connection,
            feishu_client=feishu_client,
        )


@pytest.fixture
def png_file() -> bytes:
    output = BytesIO()
    Image.new("RGB", (64, 64), "white").save(output, format="PNG")
    return output.getvalue()
