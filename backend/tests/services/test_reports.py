import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from app.core.db import Base, make_engine, make_session_factory
from app.domain.results import Finding, FindingStatus, InspectionResult, Verdict
from app.domain.states import ImageStatus, TaskStatus
from app.repositories import TaskRepository
from app.services.feishu_client import BlockRef, DocumentRef, FeishuClient
from app.services.reports import ReportBuilder


class FakeFeishuClient:
    def __init__(self) -> None:
        self.fail_after_operations: int | None = None
        self.operation_count = 0
        self.created_documents = 0
        self.seen_operation_keys: set[str] = set()
        self.duplicate_operation_keys: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self.block_payloads: list[dict] = []

    def _operation(self, kind: str, key: str) -> None:
        self.operation_count += 1
        if self.fail_after_operations is not None and self.operation_count > self.fail_after_operations:
            raise RuntimeError("injected report interruption")
        if key in self.seen_operation_keys:
            self.duplicate_operation_keys.append(key)
        self.seen_operation_keys.add(key)
        self.calls.append((kind, key))

    async def create_document(
        self,
        title: str,
        folder_token: str,
        client_token: str,
    ) -> DocumentRef:
        del title, folder_token
        self._operation("document", client_token)
        self.created_documents += 1
        return DocumentRef("document-1", "https://example.feishu.cn/docx/document-1")

    async def create_blocks(
        self,
        document_id: str,
        parent_block_id: str,
        blocks: list[dict],
        operation_key: str,
    ) -> list[BlockRef]:
        del document_id, parent_block_id
        self.block_payloads.extend(blocks)
        self._operation("blocks", operation_key)
        return [BlockRef(f"block-{self.operation_count}")]

    async def create_image_block(
        self,
        document_id: str,
        parent_block_id: str,
        operation_key: str,
    ) -> BlockRef:
        del document_id, parent_block_id
        self._operation("image-block", operation_key)
        return BlockRef(f"image-{self.operation_count}")

    async def upload_docx_image(self, block_id: str, filename: str, content: bytes) -> str:
        del content
        self._operation("upload", f"upload:{block_id}:{filename}")
        return f"file-{block_id}"


@dataclass
class ReportFixture:
    builder: ReportBuilder
    task_id: str
    feishu: FakeFeishuClient
    sessions: object


def result(verdict: Verdict, marker: str) -> InspectionResult:
    if verdict is Verdict.UNVERIFIABLE:
        return InspectionResult(
            image_valid=False,
            unverifiable_reason="image is obscured",
            verdict=verdict,
            summary=marker,
            findings=[],
        )
    finding_status = FindingStatus.FAIL if verdict is Verdict.FAIL else FindingStatus.PASS
    return InspectionResult(
        image_valid=True,
        verdict=verdict,
        summary=marker,
        findings=[
            Finding(
                rule_id="badge",
                rule_name="badge",
                status=finding_status,
                observation=marker,
                visual_evidence=f"visible-{marker}",
            )
        ],
    )


@pytest.fixture
def report_fixture(tmp_path: Path) -> ReportFixture:
    engine = make_engine(tmp_path / "reports.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions() as session:
        repo = TaskRepository(session)
        task = repo.create_task(
            "report task",
            {
                "template_name": "general",
                "template_version": 1,
                "rules": [{"rule_id": "badge", "name": "badge", "text": "badge"}],
            },
            {"model": "seed"},
        )
        for position, verdict in enumerate(
            (Verdict.PASS, Verdict.FAIL, Verdict.UNVERIFIABLE),
            start=1,
        ):
            thumbnail = tmp_path / f"{position:03}.jpg"
            thumbnail.write_bytes(f"thumbnail-{position}".encode())
            image = repo.add_image(
                task.id,
                position - 1,
                f"{position:03}.png",
                f"hash-{position}",
                str(thumbnail),
                str(thumbnail),
                str(thumbnail),
            )
            repo.save_result(
                image.id,
                result(verdict, f"result-{position}").model_dump(mode="json"),
                raw_text_redacted="",
            )
            repo.set_image_status(image.id, ImageStatus.SUCCEEDED)
        repo.set_task_status(task.id, TaskStatus.QUEUED)
        repo.set_task_status(task.id, TaskStatus.RUNNING)
        repo.set_task_status(task.id, TaskStatus.EXPORTING)
        session.commit()
    feishu = FakeFeishuClient()
    return ReportFixture(
        ReportBuilder(sessions, feishu, folder_token="folder-1"),
        task.id,
        feishu,
        sessions,
    )


def test_report_projection_matches_result_counts(report_fixture) -> None:
    projection = report_fixture.builder.project(report_fixture.task_id)

    assert projection.counts == {
        "pass": 1,
        "fail": 1,
        "unverifiable": 1,
        "failed": 0,
    }
    assert [item.filename for item in projection.items] == ["001.png", "002.png", "003.png"]


@pytest.mark.asyncio
async def test_report_does_not_render_model_confidence(report_fixture) -> None:
    await report_fixture.builder.export(report_fixture.task_id)

    rendered = json.dumps(report_fixture.feishu.block_payloads, ensure_ascii=False)
    assert "置信度" not in rendered


@pytest.mark.asyncio
async def test_retry_resumes_existing_document_without_duplicate_blocks(report_fixture) -> None:
    report_fixture.feishu.fail_after_operations = 4

    first = await report_fixture.builder.export(report_fixture.task_id)
    assert first.status == "failed"
    document_id = first.document_id

    report_fixture.feishu.fail_after_operations = None
    second = await report_fixture.builder.export(report_fixture.task_id)

    assert second.document_id == document_id
    assert second.status == "succeeded"
    assert report_fixture.feishu.created_documents == 1
    assert report_fixture.feishu.duplicate_operation_keys == []
    assert ("image-block", "image:" + report_fixture.builder.project(report_fixture.task_id).items[0].image_id) in report_fixture.feishu.calls


@pytest.mark.asyncio
async def test_new_version_creates_a_new_active_export(report_fixture) -> None:
    first = await report_fixture.builder.export(report_fixture.task_id)
    second = await report_fixture.builder.export(report_fixture.task_id, new_version=True)

    assert first.id != second.id
    assert first.document_id != ""
    assert report_fixture.feishu.created_documents == 2


def test_recover_interrupted_export_requires_explicit_retry(report_fixture) -> None:
    with report_fixture.sessions() as session:  # type: ignore[operator]
        repo = TaskRepository(session)
        export = repo.get_or_create_active_export(report_fixture.task_id)
        export.status = "running"
        export.document_id = "document-existing"
        export.cursor = 3
        session.commit()

    recovered = report_fixture.builder.recover_interrupted_exports()

    assert recovered == 1
    with report_fixture.sessions() as session:  # type: ignore[operator]
        repo = TaskRepository(session)
        export = repo.get_or_create_active_export(report_fixture.task_id)
        assert export.status == "failed"
        assert export.error_code == "report_interrupted"
        assert export.document_id == "document-existing"
        assert export.cursor == 3
        assert repo.get_task(report_fixture.task_id).status == TaskStatus.REVIEW_REQUIRED
    assert report_fixture.feishu.calls == []


def test_recover_exporting_task_without_export_record(report_fixture) -> None:
    recovered = report_fixture.builder.recover_interrupted_exports()

    assert recovered == 1
    with report_fixture.sessions() as session:  # type: ignore[operator]
        task = TaskRepository(session).get_task(report_fixture.task_id)
        assert task.status == TaskStatus.REVIEW_REQUIRED


class FakeOAuth:
    def __init__(self) -> None:
        self.calls = 0

    async def get_valid_access_token(self) -> str:
        self.calls += 1
        return f"token-{self.calls}"


class FakeRequestHttp:
    def __init__(self, responses: list[httpx.Response], delay: float = 0) -> None:
        self.responses = responses
        self.delay = delay
        self.requests: list[dict] = []
        self.active = 0
        self.max_active = 0

    async def request(self, method: str, url: str, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return self.responses.pop(0)
        finally:
            self.active -= 1


def api_response(status: int, payload: dict, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers=headers,
        request=httpx.Request("POST", "https://open.feishu.cn/open-apis/test"),
    )


@pytest.mark.asyncio
async def test_feishu_client_appends_document_xml() -> None:
    oauth = FakeOAuth()
    http = FakeRequestHttp(
        [api_response(200, {"code": 0, "data": {"result": "success"}})]
    )
    client = FeishuClient(oauth, http=http, sleep=lambda _seconds: asyncio.sleep(0))

    await client.append_document_xml("doc-1", "<table></table>", "matrix:1")

    request = http.requests[0]
    assert request["method"] == "PUT"
    assert request["url"].endswith("/open-apis/docs_ai/v1/documents/doc-1")
    assert request["json"] == {
        "block_id": "-1",
        "command": "block_insert_after",
        "content": "<table></table>",
        "format": "xml",
        "revision_id": -1,
    }


@pytest.mark.asyncio
async def test_feishu_client_retries_rate_limit_with_fresh_token() -> None:
    oauth = FakeOAuth()
    http = FakeRequestHttp(
        [
            api_response(429, {"code": 999}, {"retry-after": "0"}),
            api_response(
                200,
                {
                    "code": 0,
                    "data": {"document": {"document_id": "doc-1"}},
                },
            ),
        ]
    )
    client = FeishuClient(oauth, http=http, sleep=lambda _seconds: asyncio.sleep(0))

    document = await client.create_document("title", "folder", "client-token")

    assert document.id == "doc-1"
    assert len(http.requests) == 2
    assert oauth.calls == 2
    assert http.requests[0]["headers"]["Authorization"] == "Bearer token-1"
    assert http.requests[1]["headers"]["Authorization"] == "Bearer token-2"


@pytest.mark.asyncio
async def test_feishu_media_uploads_are_serial() -> None:
    oauth = FakeOAuth()
    http = FakeRequestHttp(
        [
            api_response(200, {"code": 0, "data": {"file_token": "file-1"}}),
            api_response(200, {"code": 0, "data": {"file_token": "file-2"}}),
        ],
        delay=0.01,
    )
    client = FeishuClient(oauth, http=http, sleep=lambda _seconds: asyncio.sleep(0))

    await asyncio.gather(
        client.upload_docx_image("block-1", "one.jpg", b"one"),
        client.upload_docx_image("block-2", "two.jpg", b"two"),
    )

    assert http.max_active == 1
