import asyncio
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.core.db import Base, make_engine, make_session_factory
from app.domain.results import Finding, FindingStatus, InspectionResult, Verdict
from app.domain.states import ImageStatus, TaskStatus
from app.domain.usage import UsageAvailability
from app.repositories import REPORT_LAYOUT_OPERATION, REPORT_WORKSHEET_ID_KEY, TaskRepository
from app.services.feishu_client import SpreadsheetRef
from app.services.reports import ReportBuilder
from tests.usage_fixtures import successful_usage


@dataclass(frozen=True)
class ImageCall:
    spreadsheet_token: str
    sheet_id: str
    cell: str
    image_path: Path
    filename: str


class FakeFeishuClient:
    def __init__(self) -> None:
        self.created_spreadsheets = 0
        self.values: list[list[str]] = []
        self.styles: dict = {}
        self.image_calls: list[ImageCall] = []
        self.cell_attempts: Counter[str] = Counter()
        self.fail_cells: set[str] = set()
        self.delay = 0.0
        self.active_uploads = 0
        self.max_active_uploads = 0

    async def create_spreadsheet(
        self,
        title: str,
        folder_token: str,
        values: list[list[str]],
        styles: dict,
    ) -> SpreadsheetRef:
        assert title.startswith("report task 巡检报告")
        assert folder_token == "folder-1"
        self.created_spreadsheets += 1
        self.values = values
        self.styles = styles
        token = f"spreadsheet-{self.created_spreadsheets}"
        return SpreadsheetRef(
            token,
            f"https://example.feishu.cn/sheets/{token}",
            f"worksheet-{self.created_spreadsheets}",
        )

    async def set_cell_image(
        self,
        spreadsheet_token: str,
        sheet_id: str,
        cell: str,
        image_path: Path,
        filename: str,
    ) -> None:
        self.cell_attempts[cell] += 1
        self.active_uploads += 1
        self.max_active_uploads = max(self.max_active_uploads, self.active_uploads)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if cell in self.fail_cells:
                raise RuntimeError("injected cell image failure")
            assert image_path.is_file()
            self.image_calls.append(
                ImageCall(spreadsheet_token, sheet_id, cell, image_path, filename)
            )
        finally:
            self.active_uploads -= 1


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
async def test_report_creates_spreadsheet_and_places_matching_images(report_fixture) -> None:
    exported = await report_fixture.builder.export(report_fixture.task_id)

    assert exported.status == "succeeded"
    assert exported.document_id == "spreadsheet-1"
    assert exported.document_url == "https://example.feishu.cn/sheets/spreadsheet-1"
    assert exported.remote_blocks_json[REPORT_WORKSHEET_ID_KEY] == "worksheet-1"
    assert exported.completed_operations_json[0] == REPORT_LAYOUT_OPERATION
    assert set(exported.completed_operations_json[1:]) == {
        f"sheet-image:{item.image_id}"
        for item in report_fixture.builder.project(report_fixture.task_id).items
    }
    assert report_fixture.feishu.created_spreadsheets == 1
    assert report_fixture.feishu.values[0] == ["巡检项", "安保", "", "前厅"]
    assert report_fixture.feishu.values[1] == ["巡检图片", "", "", ""]
    assert report_fixture.feishu.values[2] == [
        "巡检结果",
        "合格",
        "不合格\nresult-2",
        "无法判断\nimage is obscured",
    ]
    assert report_fixture.feishu.values[3] == ["模型 Token 用量", "", "", ""]
    assert report_fixture.feishu.values[4] == [
        (
            "总 Token：36\n"
            "输入：30\n"
            "缓存命中：12\n"
            "未缓存输入：18\n"
            "输出：6\n"
            "推理 Token：0（包含在输出中）\n"
            "已知调用：3"
        ),
        "",
        "",
        "",
    ]
    rendered = "\n".join(cell for row in report_fixture.feishu.values for cell in row)
    assert "raw_usage" not in rendered
    assert "费用" not in rendered
    assert [call.cell for call in report_fixture.feishu.image_calls] == ["B2", "C2", "D2"]
    assert all(call.spreadsheet_token == "spreadsheet-1" for call in report_fixture.feishu.image_calls)
    assert all(call.sheet_id == "worksheet-1" for call in report_fixture.feishu.image_calls)


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
    body = report_fixture.feishu.values[-1][0]
    assert body.splitlines()[0] == "已知总 Token：36"
    assert "未返回、格式异常或未采集 usage：至少 1 次；不计入已知总量。" in body
    assert "总 Token：0" not in body


@pytest.mark.asyncio
async def test_retry_reuses_workbook_and_skips_completed_cell_images(report_fixture) -> None:
    report_fixture.feishu.fail_cells.add("C2")

    first = await report_fixture.builder.export(report_fixture.task_id)
    report_fixture.feishu.fail_cells.clear()
    second = await report_fixture.builder.export(report_fixture.task_id)

    assert first.status == "failed"
    assert second.status == "succeeded"
    assert first.document_id == second.document_id == "spreadsheet-1"
    assert report_fixture.feishu.created_spreadsheets == 1
    assert report_fixture.feishu.cell_attempts == Counter({"C2": 2, "B2": 1, "D2": 1})


@pytest.mark.asyncio
async def test_cell_image_uploads_use_bounded_concurrency(report_fixture) -> None:
    report_fixture.feishu.delay = 0.01

    exported = await report_fixture.builder.export(report_fixture.task_id)

    assert exported.status == "succeeded"
    assert 1 < report_fixture.feishu.max_active_uploads <= 4


@pytest.mark.asyncio
async def test_new_version_creates_a_new_spreadsheet(report_fixture) -> None:
    first = await report_fixture.builder.export(report_fixture.task_id)
    second = await report_fixture.builder.export(report_fixture.task_id, new_version=True)

    assert first.id != second.id
    assert first.document_id == "spreadsheet-1"
    assert second.document_id == "spreadsheet-2"
    assert report_fixture.feishu.created_spreadsheets == 2


def test_recover_interrupted_export_requires_explicit_retry(report_fixture) -> None:
    with report_fixture.sessions() as session:  # type: ignore[operator]
        repo = TaskRepository(session)
        export = repo.get_or_create_active_export(report_fixture.task_id)
        export.status = "running"
        export.document_id = "spreadsheet-existing"
        export.remote_blocks_json = {REPORT_WORKSHEET_ID_KEY: "worksheet-existing"}
        export.cursor = 3
        session.commit()

    recovered = report_fixture.builder.recover_interrupted_exports()

    assert recovered == 1
    with report_fixture.sessions() as session:  # type: ignore[operator]
        repo = TaskRepository(session)
        export = repo.get_or_create_active_export(report_fixture.task_id)
        assert export.status == "failed"
        assert export.error_code == "report_interrupted"
        assert export.document_id == "spreadsheet-existing"
        assert export.remote_blocks_json[REPORT_WORKSHEET_ID_KEY] == "worksheet-existing"
        assert export.cursor == 3
        assert repo.get_task(report_fixture.task_id).status == TaskStatus.REVIEW_REQUIRED
    assert report_fixture.feishu.image_calls == []


def test_recover_exporting_task_without_export_record(report_fixture) -> None:
    recovered = report_fixture.builder.recover_interrupted_exports()

    assert recovered == 1
    with report_fixture.sessions() as session:  # type: ignore[operator]
        task = TaskRepository(session).get_task(report_fixture.task_id)
        assert task.status == TaskStatus.REVIEW_REQUIRED
