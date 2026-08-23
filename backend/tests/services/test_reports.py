import asyncio
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.core.db import Base, make_engine, make_session_factory
from app.domain.results import Finding, FindingStatus, InspectionResult, Verdict
from app.domain.states import ImageStatus, TaskStatus
from app.domain.usage import UsageAvailability
from app.repositories import TaskRepository
from app.services.feishu_client import BlockRef, DocumentRef
from app.services.reports import ReportBuilder
from tests.usage_fixtures import successful_usage


@dataclass(frozen=True)
class ImageCall:
    block_id: str
    filename: str
    content: bytes


class FakeFeishuClient:
    def __init__(self) -> None:
        self.created_documents = 0
        self.appended_xml: list[str] = []
        self.fetch_count = 0
        self.image_parent_ids: list[str] = []
        self.move_calls: list[tuple[str, str]] = []
        self.image_calls: list[ImageCall] = []
        self.bind_calls: list[tuple[str, str, str]] = []
        self.block_attempts: Counter[str] = Counter()
        self.upload_attempts: Counter[str] = Counter()
        self.bind_attempts: Counter[str] = Counter()
        self.fail_blocks: set[str] = set()
        self.fail_bind_blocks: set[str] = set()
        self.delay = 0.0
        self.bind_delay = 0.0
        self.block_delay = 0.0
        self.active_uploads = 0
        self.max_active_uploads = 0
        self.active_bindings = 0
        self.max_active_bindings = 0
        self.active_block_creates = 0
        self.max_active_block_creates = 0
        self.reject_concurrent_bindings = False
        self.reject_concurrent_block_creates = False

    async def create_document(
        self,
        title: str,
        folder_token: str,
        client_token: str,
    ) -> DocumentRef:
        assert title.startswith("report task 巡检报告")
        assert folder_token == "folder-1"
        assert client_token
        self.created_documents += 1
        token = f"document-{self.created_documents}"
        return DocumentRef(
            token,
            f"https://example.feishu.cn/docx/{token}",
        )

    async def append_document_xml(
        self,
        document_id: str,
        content: str,
        operation_key: str,
    ) -> None:
        assert document_id.startswith("document-")
        assert operation_key == "doc:layout"
        self.appended_xml.append(content)

    async def fetch_document_xml(self, document_id: str) -> str:
        assert document_id.startswith("document-")
        self.fetch_count += 1
        return """
        <table id="table-1">
          <thead><tr><th id="a1"><p>巡检项</p></th><th id="b1"><p>安保</p></th><th id="d1"><p>前厅</p></th></tr></thead>
          <tbody>
            <tr><td><p id="label-a">巡检图片</p></td><td><p id="anchor-b"></p></td><td><p id="anchor-c"></p></td><td><p id="anchor-d"></p></td></tr>
            <tr><td id="a3"><p>巡检结果</p></td><td id="b3"><p>合格</p></td><td id="c3"><p>不合格</p></td><td id="d3"><p>无法判断</p></td></tr>
          </tbody>
        </table>
        """

    async def create_image_block(
        self,
        document_id: str,
        parent_block_id: str,
        operation_key: str,
    ) -> BlockRef:
        assert document_id.startswith("document-")
        self.active_block_creates += 1
        self.max_active_block_creates = max(
            self.max_active_block_creates,
            self.active_block_creates,
        )
        try:
            if self.block_delay:
                await asyncio.sleep(self.block_delay)
            if self.reject_concurrent_block_creates and self.active_block_creates > 1:
                raise RuntimeError("concurrent document block creation")
            self.block_attempts[operation_key] += 1
            self.image_parent_ids.append(parent_block_id)
            return BlockRef(f"image-{operation_key.rsplit(':', 1)[-1]}")
        finally:
            self.active_block_creates -= 1

    async def upload_docx_image(
        self,
        block_id: str,
        filename: str,
        content: bytes,
    ) -> str:
        self.upload_attempts[block_id] += 1
        self.active_uploads += 1
        self.max_active_uploads = max(self.max_active_uploads, self.active_uploads)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if block_id in self.fail_blocks:
                raise RuntimeError("injected document image failure")
            self.image_calls.append(ImageCall(block_id, filename, content))
            return f"token-{block_id}"
        finally:
            self.active_uploads -= 1

    async def bind_docx_image(
        self,
        document_id: str,
        block_id: str,
        file_token: str,
    ) -> None:
        assert document_id.startswith("document-")
        self.bind_attempts[block_id] += 1
        self.active_bindings += 1
        self.max_active_bindings = max(
            self.max_active_bindings,
            self.active_bindings,
        )
        try:
            if self.bind_delay:
                await asyncio.sleep(self.bind_delay)
            if self.reject_concurrent_bindings and self.active_bindings > 1:
                raise RuntimeError("concurrent document image binding")
            if block_id in self.fail_bind_blocks:
                raise RuntimeError("injected document image binding failure")
            self.bind_calls.append((document_id, block_id, file_token))
        finally:
            self.active_bindings -= 1

    async def move_document_block(
        self,
        document_id: str,
        anchor_block_id: str,
        source_block_id: str,
    ) -> None:
        assert document_id.startswith("document-")
        self.move_calls.append((anchor_block_id, source_block_id))


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
        filenames = (
            "行02_D列_安保_01.png",
            "行02_E列_安保_02.png",
            "行02_F列_前厅_01.png",
        )
        verdicts = (Verdict.PASS, Verdict.FAIL, Verdict.UNVERIFIABLE)
        for position, (filename, verdict) in enumerate(
            zip(filenames, verdicts, strict=True),
            start=1,
        ):
            thumbnail = tmp_path / f"{position:03}.jpg"
            thumbnail.write_bytes(f"thumbnail-{position}".encode())
            image = repo.add_image(
                task.id,
                position - 1,
                filename,
                f"hash-{position}",
                str(thumbnail),
                str(thumbnail),
                str(thumbnail),
            )
            image.attempt_count = 1
            repo.record_attempt(
                image.id,
                status="succeeded",
                attempt_number=1,
                usage=successful_usage("responses").model_dump(mode="json"),
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


def test_report_projection_matches_result_counts_and_usage(report_fixture) -> None:
    projection = report_fixture.builder.project(report_fixture.task_id)

    assert projection.counts == {
        "pass": 1,
        "fail": 1,
        "unverifiable": 1,
        "failed": 0,
    }
    assert projection.token_usage.availability is UsageAvailability.COMPLETE
    assert projection.token_usage.total_tokens == 36
    assert [item.filename for item in projection.items] == [
        "行02_D列_安保_01.png",
        "行02_E列_安保_02.png",
        "行02_F列_前厅_01.png",
    ]


@pytest.mark.asyncio
async def test_report_creates_docx_table_and_places_matching_images(report_fixture) -> None:
    exported = await report_fixture.builder.export(report_fixture.task_id)

    assert exported.status == "succeeded"
    assert exported.document_id == "document-1"
    assert exported.document_url == "https://example.feishu.cn/docx/document-1"
    assert exported.completed_operations_json[0] == "doc:layout"
    image_ids = {
        item.image_id
        for item in report_fixture.builder.project(report_fixture.task_id).items
    }
    assert set(exported.completed_operations_json[1:]) == {
        *(f"doc-image:{image_id}" for image_id in image_ids),
        *(f"doc-image-bound:{image_id}" for image_id in image_ids),
    }
    assert report_fixture.feishu.created_documents == 1
    assert len(report_fixture.feishu.appended_xml) == 1
    rendered = report_fixture.feishu.appended_xml[0]
    assert "巡检项" in rendered
    assert "巡检图片" in rendered
    assert "巡检结果" in rendered
    assert "模型完整输出" in rendered
    assert "不合格<br/>result-2" in rendered
    assert "无法判断<br/>image is obscured" in rendered
    assert "检查项 1：badge｜不合格" in rendered
    assert "观察：</b>result-2" in rendered
    assert "视觉证据：</b>visible-result-2" in rendered
    assert "图片有效性：</b>无效" in rendered
    assert "不可判断原因：</b>image is obscured" in rendered
    assert "总 Token：36" in rendered
    assert "raw_usage" not in rendered
    assert "费用" not in rendered
    assert report_fixture.feishu.image_parent_ids == ["document-1"] * 3
    assert {anchor for anchor, _block in report_fixture.feishu.move_calls} == {
        "anchor-b",
        "anchor-c",
        "anchor-d",
    }
    assert {call.block_id for call in report_fixture.feishu.image_calls} == {
        f"image-{item.image_id}"
        for item in report_fixture.builder.project(report_fixture.task_id).items
    }
    assert {
        (block_id, token)
        for _document_id, block_id, token in report_fixture.feishu.bind_calls
    } == {
        (f"image-{item.image_id}", f"token-image-{item.image_id}")
        for item in report_fixture.builder.project(report_fixture.task_id).items
    }
    assert all(call.content.startswith(b"thumbnail-") for call in report_fixture.feishu.image_calls)


@pytest.mark.asyncio
async def test_report_renders_partial_usage_as_known_only(report_fixture) -> None:
    with report_fixture.sessions() as session:  # type: ignore[operator]
        repo = TaskRepository(session)
        image = repo.list_images(report_fixture.task_id)[0]
        image.attempt_count = 2
        repo.record_attempt(image.id, "failed", 2, usage=None)
        session.commit()

    projection = report_fixture.builder.project(report_fixture.task_id)
    exported = await report_fixture.builder.export(report_fixture.task_id)

    assert exported.status == "succeeded"
    assert projection.token_usage.availability is UsageAvailability.PARTIAL
    assert projection.token_usage.total_tokens == 36
    body = report_fixture.feishu.appended_xml[0]
    assert "已知总 Token：36" in body
    assert "未返回、格式异常或未采集 usage：至少 1 次；不计入已知总量。" in body
    assert "总 Token：0" not in body


@pytest.mark.asyncio
async def test_retry_reuses_document_blocks_and_skips_completed_images(report_fixture) -> None:
    image_ids = [
        item.image_id
        for item in report_fixture.builder.project(report_fixture.task_id).items
    ]
    failed_block = f"image-{image_ids[1]}"
    report_fixture.feishu.fail_blocks.add(failed_block)

    first = await report_fixture.builder.export(report_fixture.task_id)
    report_fixture.feishu.fail_blocks.clear()
    second = await report_fixture.builder.export(report_fixture.task_id)

    assert first.status == "failed"
    assert second.status == "succeeded"
    assert first.document_id == second.document_id == "document-1"
    assert report_fixture.feishu.created_documents == 1
    assert len(report_fixture.feishu.appended_xml) == 1
    assert report_fixture.feishu.fetch_count == 1
    assert set(report_fixture.feishu.block_attempts.values()) == {1}
    assert report_fixture.feishu.upload_attempts == Counter(
        {
            failed_block: 2,
            f"image-{image_ids[0]}": 1,
            f"image-{image_ids[2]}": 1,
        }
    )


@pytest.mark.asyncio
async def test_retry_reuses_block_and_reuploads_when_image_binding_fails(
    report_fixture,
) -> None:
    image_ids = [
        item.image_id
        for item in report_fixture.builder.project(report_fixture.task_id).items
    ]
    failed_block = f"image-{image_ids[1]}"
    report_fixture.feishu.fail_bind_blocks.add(failed_block)

    first = await report_fixture.builder.export(report_fixture.task_id)
    with report_fixture.sessions() as session:  # type: ignore[operator]
        completed = set(
            TaskRepository(session)
            .get_or_create_active_export(report_fixture.task_id)
            .completed_operations_json
        )
    report_fixture.feishu.fail_bind_blocks.clear()
    second = await report_fixture.builder.export(report_fixture.task_id)

    assert first.status == "failed"
    assert f"doc-image-bound:{image_ids[1]}" not in completed
    assert second.status == "succeeded"
    assert set(report_fixture.feishu.block_attempts.values()) == {1}
    assert report_fixture.feishu.upload_attempts == Counter(
        {
            failed_block: 2,
            f"image-{image_ids[0]}": 1,
            f"image-{image_ids[2]}": 1,
        }
    )
    assert report_fixture.feishu.bind_attempts == report_fixture.feishu.upload_attempts


@pytest.mark.asyncio
async def test_legacy_completed_images_are_bound_without_redundant_move(
    report_fixture,
) -> None:
    image_ids = [
        item.image_id
        for item in report_fixture.builder.project(report_fixture.task_id).items
    ]
    with report_fixture.sessions() as session:  # type: ignore[operator]
        export = TaskRepository(session).get_or_create_active_export(
            report_fixture.task_id
        )
        export.status = "succeeded"
        export.document_id = "document-existing"
        export.document_url = "https://example.feishu.cn/docx/document-existing"
        export.completed_operations_json = [
            "doc:layout",
            *(f"doc-image:{image_id}" for image_id in image_ids),
        ]
        export.remote_blocks_json = {
            **{
                f"doc-cell:{image_id}": f"anchor-{index}"
                for index, image_id in enumerate(image_ids)
            },
            **{
                f"doc-image-block:{image_id}": f"image-{image_id}"
                for image_id in image_ids
            },
        }
        session.commit()

    exported = await report_fixture.builder.export(report_fixture.task_id)

    assert exported.status == "succeeded"
    assert report_fixture.feishu.block_attempts == Counter()
    assert report_fixture.feishu.move_calls == []
    assert set(exported.completed_operations_json) >= {
        f"doc-image-bound:{image_id}" for image_id in image_ids
    }


@pytest.mark.asyncio
async def test_document_image_uploads_use_bounded_concurrency(report_fixture) -> None:
    report_fixture.feishu.delay = 0.01

    exported = await report_fixture.builder.export(report_fixture.task_id)

    assert exported.status == "succeeded"
    assert 1 < report_fixture.feishu.max_active_uploads <= 4


@pytest.mark.asyncio
async def test_document_image_bindings_are_serialized(report_fixture) -> None:
    report_fixture.feishu.bind_delay = 0.01
    report_fixture.feishu.reject_concurrent_bindings = True

    exported = await report_fixture.builder.export(report_fixture.task_id)

    assert exported.status == "succeeded"
    assert report_fixture.feishu.max_active_bindings == 1


@pytest.mark.asyncio
async def test_document_image_blocks_are_created_serially(report_fixture) -> None:
    report_fixture.feishu.block_delay = 0.01
    report_fixture.feishu.reject_concurrent_block_creates = True

    exported = await report_fixture.builder.export(report_fixture.task_id)

    assert exported.status == "succeeded"
    assert report_fixture.feishu.max_active_block_creates == 1


@pytest.mark.asyncio
async def test_new_version_creates_a_new_document(report_fixture) -> None:
    first = await report_fixture.builder.export(report_fixture.task_id)
    second = await report_fixture.builder.export(report_fixture.task_id, new_version=True)

    assert first.id != second.id
    assert first.document_id == "document-1"
    assert second.document_id == "document-2"
    assert report_fixture.feishu.created_documents == 2


def test_recover_interrupted_export_requires_explicit_retry(report_fixture) -> None:
    with report_fixture.sessions() as session:  # type: ignore[operator]
        repo = TaskRepository(session)
        export = repo.get_or_create_active_export(report_fixture.task_id)
        export.status = "running"
        export.document_id = "document-existing"
        export.remote_blocks_json = {"doc-cell:image-1": "cell-existing"}
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
        assert export.remote_blocks_json["doc-cell:image-1"] == "cell-existing"
        assert export.cursor == 3
        assert repo.get_task(report_fixture.task_id).status == TaskStatus.REVIEW_REQUIRED
    assert report_fixture.feishu.image_calls == []


def test_recover_exporting_task_without_export_record(report_fixture) -> None:
    recovered = report_fixture.builder.recover_interrupted_exports()

    assert recovered == 1
    with report_fixture.sessions() as session:  # type: ignore[operator]
        task = TaskRepository(session).get_task(report_fixture.task_id)
        assert task.status == TaskStatus.REVIEW_REQUIRED
