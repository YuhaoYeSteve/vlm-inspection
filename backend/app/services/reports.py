import asyncio
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.domain.results import InspectionResult
from app.domain.states import ReportStatus, TaskStatus
from app.domain.usage import TokenUsageSummary, summarize_attempt_payloads
from app.models import ReportExport
from app.repositories import REPORT_LAYOUT_OPERATION, TaskRepository
from app.services.events import TaskEventBroker
from app.services.feishu_client import FeishuClientProtocol
from app.services.feishu_oauth import FeishuReauthorizationRequired
from app.services.lark_cli import FeishuConnectionProviderProtocol
from app.services.report_document import (
    DocumentImagePlacement,
    DocumentItem,
    parse_image_cell_ids,
    render_report_document,
)


@dataclass(frozen=True)
class ReportItem:
    image_id: str
    position: int
    filename: str
    thumbnail_path: str
    execution_status: str
    result: InspectionResult | None


@dataclass(frozen=True)
class RuleSummary:
    rule_id: str
    rule_name: str
    failures: int
    observations: tuple[str, ...]


@dataclass(frozen=True)
class ReportProjection:
    task_id: str
    task_name: str
    template_name: str
    template_version: int
    model: str
    created_at: datetime
    counts: dict[str, int]
    token_usage: TokenUsageSummary
    rules: tuple[RuleSummary, ...]
    items: tuple[ReportItem, ...]
    snapshot_hash: str


class ReportBuilder:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        feishu: FeishuClientProtocol,
        folder_token: str = "",
        image_upload_concurrency: int = 4,
    ) -> None:
        if image_upload_concurrency < 1:
            raise ValueError("image_upload_concurrency must be positive")
        self.sessions = sessions
        self.feishu = feishu
        self.folder_token = folder_token
        self.image_upload_concurrency = image_upload_concurrency

    def project(self, task_id: str) -> ReportProjection:
        with self.sessions() as session:
            repo = TaskRepository(session)
            task = repo.get_task(task_id)
            images = repo.list_images(task_id)
            attempts = repo.list_attempts_for_tasks([task.id])[task.id]
            items: list[ReportItem] = []
            counts = {"pass": 0, "fail": 0, "unverifiable": 0, "failed": 0}
            rule_counts: Counter[tuple[str, str]] = Counter()
            observations: dict[tuple[str, str], list[str]] = defaultdict(list)
            for image in images:
                record = repo.get_current_result(image.id)
                parsed = (
                    None
                    if record is None
                    else InspectionResult.model_validate(record.result_json).normalized()
                )
                if image.status == "failed":
                    counts["failed"] += 1
                elif parsed is not None:
                    counts[parsed.verdict.value] += 1
                    for finding in parsed.findings:
                        if finding.status.value == "fail":
                            key = (finding.rule_id, finding.rule_name)
                            rule_counts[key] += 1
                            observations[key].append(finding.observation)
                items.append(
                    ReportItem(
                        image_id=image.id,
                        position=image.position,
                        filename=image.original_name,
                        thumbnail_path=image.thumbnail_path,
                        execution_status=image.status,
                        result=parsed,
                    )
                )
            template = task.template_snapshot_json
            model = task.model_snapshot_json
            snapshot_hash = hashlib.sha256(
                json.dumps(template, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            rules = tuple(
                RuleSummary(key[0], key[1], count, tuple(observations[key]))
                for key, count in sorted(rule_counts.items())
            )
            return ReportProjection(
                task_id=task.id,
                task_name=task.name,
                template_name=str(template.get("template_name", "")),
                template_version=int(template.get("template_version", 0)),
                model=str(model.get("model", "")),
                created_at=task.created_at,
                counts=counts,
                token_usage=summarize_attempt_payloads(
                    [attempt.usage_json for attempt in attempts]
                ),
                rules=rules,
                items=tuple(items),
                snapshot_hash=snapshot_hash,
            )

    async def export(self, task_id: str, new_version: bool = False) -> ReportExport:
        export_id = self.prepare_export(task_id, new_version)
        return await self.export_prepared(export_id)

    async def export_prepared(self, export_id: str) -> ReportExport:
        try:
            await self._write_export(export_id)
        except (
            FeishuReauthorizationRequired,
            KeyError,
            OSError,
            RuntimeError,
            ValueError,
        ):
            self._mark_failed(export_id, "report_export_failed")
        return self._get_export(export_id)

    def recover_interrupted_exports(self) -> int:
        with self.sessions() as session:
            recovered = TaskRepository(session).recover_interrupted_exports()
            session.commit()
            return recovered

    def prepare_export(self, task_id: str, new_version: bool = False) -> str:
        with self.sessions() as session:
            repo = TaskRepository(session)
            task = repo.get_task(task_id)
            if TaskStatus(task.status) is TaskStatus.REVIEW_REQUIRED:
                repo.set_task_status(task_id, TaskStatus.EXPORTING)
            export = (
                repo.create_new_export(task_id)
                if new_version
                else repo.get_or_create_active_export(task_id)
            )
            export.status = ReportStatus.RUNNING.value
            export.error_code = None
            session.commit()
            return export.id

    async def _write_export(self, export_id: str) -> None:
        export = self._get_export(export_id)
        projection = self.project(export.task_id)
        layout = render_report_document(
            tuple(self._document_item(item) for item in projection.items),
            token_usage=projection.token_usage,
        )
        if export.document_id is None:
            folder_token = self._folder_token()
            timestamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M")
            title = f"{projection.task_name} 巡检报告 {timestamp}"
            document = await self.feishu.create_document(
                title,
                folder_token,
                export.id,
            )
            self._save_document(export.id, document.id, document.url)
            export = self._get_export(export.id)
        assert export.document_id is not None
        document_id = export.document_id
        if not self._operation_completed(export.id, REPORT_LAYOUT_OPERATION):
            await self.feishu.append_document_xml(
                document_id,
                layout.xml,
                REPORT_LAYOUT_OPERATION,
            )
            self._mark_operation_completed(export.id, REPORT_LAYOUT_OPERATION)
            export = self._get_export(export.id)
        image_cells = await self._ensure_image_cells(export, layout.images)
        await self._write_document_images(
            export,
            document_id,
            layout.images,
            image_cells,
        )
        self._mark_succeeded(export.id)

    @staticmethod
    def _document_item(item: ReportItem) -> DocumentItem:
        result = item.result
        return DocumentItem(
            image_id=item.image_id,
            filename=item.filename,
            image_path=Path(item.thumbnail_path),
            verdict=result.verdict.value if result is not None else "failed",
            summary=result.summary if result is not None else "",
            unverifiable_reason=(
                result.unverifiable_reason if result is not None else None
            ),
            execution_status=item.execution_status,
        )

    async def _ensure_image_cells(
        self,
        export: ReportExport,
        placements: tuple[DocumentImagePlacement, ...],
    ) -> dict[str, str]:
        existing = {
            placement.image_id: self._remote_block(
                export.id,
                f"doc-cell:{placement.image_id}",
            )
            for placement in placements
        }
        if all(existing.values()):
            return {image_id: cell_id for image_id, cell_id in existing.items() if cell_id}

        assert export.document_id is not None
        content = await self.feishu.fetch_document_xml(export.document_id)
        cell_ids = parse_image_cell_ids(content, expected_count=len(placements))
        for placement, cell_id in zip(placements, cell_ids, strict=True):
            key = f"doc-cell:{placement.image_id}"
            if existing[placement.image_id] is None:
                self._save_remote_block(export.id, key, cell_id)
            existing[placement.image_id] = cell_id
        return {image_id: cell_id for image_id, cell_id in existing.items() if cell_id}

    async def _write_document_images(
        self,
        export: ReportExport,
        document_id: str,
        placements: tuple[DocumentImagePlacement, ...],
        image_cells: dict[str, str],
    ) -> None:
        pending = [
            placement
            for placement in placements
            if not self._operation_completed(export.id, placement.operation_key)
        ]
        semaphore = asyncio.Semaphore(self.image_upload_concurrency)
        ledger_lock = asyncio.Lock()

        async def upload(placement: DocumentImagePlacement) -> None:
            async with semaphore:
                block_key = f"doc-image-block:{placement.image_id}"
                block_id = self._remote_block(export.id, block_key)
                if block_id is None:
                    block = await self.feishu.create_image_block(
                        document_id,
                        image_cells[placement.image_id],
                        block_key,
                    )
                    block_id = block.id
                    self._save_remote_block(export.id, block_key, block_id)
                await self.feishu.upload_docx_image(
                    block_id,
                    placement.filename,
                    placement.path.read_bytes(),
                )
            async with ledger_lock:
                self._mark_operation_completed(export.id, placement.operation_key)

        results = await asyncio.gather(
            *(upload(placement) for placement in pending),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                raise result

    def _folder_token(self) -> str:
        if not self.folder_token:
            raise ReportNotReady("Feishu report destination is not configured")
        return self.folder_token

    def _get_export(self, export_id: str) -> ReportExport:
        with self.sessions() as session:
            return TaskRepository(session).get_export(export_id)

    def _save_document(
        self,
        export_id: str,
        document_id: str,
        document_url: str,
    ) -> None:
        with self.sessions() as session:
            TaskRepository(session).save_export_document(
                export_id,
                document_id,
                document_url,
            )
            session.commit()

    def _operation_completed(self, export_id: str, operation_key: str) -> bool:
        with self.sessions() as session:
            return TaskRepository(session).operation_completed(export_id, operation_key)

    def _mark_operation_completed(self, export_id: str, operation_key: str) -> None:
        with self.sessions() as session:
            TaskRepository(session).mark_operation_completed(export_id, operation_key)
            session.commit()

    def _remote_block(self, export_id: str, operation_key: str) -> str | None:
        with self.sessions() as session:
            value = TaskRepository(session).get_export(export_id).remote_blocks_json.get(
                operation_key
            )
            return value if isinstance(value, str) and value else None

    def _save_remote_block(
        self,
        export_id: str,
        operation_key: str,
        block_id: str,
    ) -> None:
        with self.sessions() as session:
            TaskRepository(session).save_remote_block(export_id, operation_key, block_id)
            session.commit()

    def _mark_succeeded(self, export_id: str) -> None:
        with self.sessions() as session:
            repo = TaskRepository(session)
            export = repo.get_export(export_id)
            export.status = ReportStatus.SUCCEEDED.value
            export.error_code = None
            task = repo.get_task(export.task_id)
            if TaskStatus(task.status) is TaskStatus.EXPORTING:
                repo.set_task_status(task.id, TaskStatus.COMPLETED)
            session.commit()

    def _mark_failed(self, export_id: str, error_code: str) -> None:
        with self.sessions() as session:
            repo = TaskRepository(session)
            export = repo.get_export(export_id)
            export.status = ReportStatus.FAILED.value
            export.error_code = error_code
            task = repo.get_task(export.task_id)
            if TaskStatus(task.status) is TaskStatus.EXPORTING:
                repo.set_task_status(task.id, TaskStatus.REVIEW_REQUIRED)
            session.commit()


class ReportNotReady(RuntimeError):
    pass


class ReportConfirmationRequired(RuntimeError):
    pass


class ReportCoordinator:
    def __init__(
        self,
        builder: ReportBuilder,
        connection_provider: FeishuConnectionProviderProtocol,
        events: TaskEventBroker,
    ) -> None:
        self.builder = builder
        self.connection_provider = connection_provider
        self.events = events
        self._active: dict[str, asyncio.Task[None]] = {}

    async def is_ready(self) -> bool:
        try:
            status = await self.connection_provider.status()
        except RuntimeError:
            return False
        return status.configured and status.connected

    async def enqueue_auto(self, task_id: str) -> str | None:
        if not await self.is_ready():
            return None
        return await self.enqueue(task_id)

    async def enqueue(
        self,
        task_id: str,
        include_failures: bool = False,
        new_version: bool = False,
    ) -> str:
        if not await self.is_ready():
            raise ReportNotReady("Feishu authorization is not ready")
        projection = self.builder.project(task_id)
        if projection.counts["failed"] and not include_failures:
            raise ReportConfirmationRequired("failed images require explicit confirmation")
        active = self._active.get(task_id)
        if active is not None and not active.done():
            with self.builder.sessions() as session:
                export = TaskRepository(session).get_active_export(task_id)
                if export is None:
                    raise ReportNotReady("report export is initializing")
                return export.id
        export_id = self.builder.prepare_export(task_id, new_version=new_version)
        task = asyncio.create_task(self._run(task_id, export_id))
        self._active[task_id] = task
        task.add_done_callback(lambda finished: self._discard(task_id, finished))
        return export_id

    async def wait_until_idle(self, task_id: str) -> None:
        active = self._active.get(task_id)
        if active is not None and not active.done():
            await asyncio.shield(active)

    async def shutdown(self) -> None:
        active = [task for task in self._active.values() if not task.done()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        self._active.clear()

    async def _run(self, task_id: str, export_id: str) -> None:
        export = await self.builder.export_prepared(export_id)
        await self.events.publish(
            task_id,
            {
                "type": f"report.{export.status}",
                "task_id": task_id,
                "export_id": export.id,
            },
        )
        with self.builder.sessions() as session:
            status = TaskRepository(session).get_task(task_id).status
        await self.events.publish(
            task_id,
            {"type": "task.updated", "task_id": task_id, "status": status},
        )

    def _discard(self, task_id: str, finished: asyncio.Task[None]) -> None:
        if self._active.get(task_id) is finished:
            self._active.pop(task_id, None)
