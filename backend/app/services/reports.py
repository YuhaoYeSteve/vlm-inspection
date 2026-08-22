import asyncio
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.domain.results import InspectionResult
from app.domain.states import ReportStatus, TaskStatus
from app.models import ReportExport
from app.repositories import SettingsRepository, TaskRepository
from app.services.events import TaskEventBroker
from app.services.feishu_client import FeishuApiError, FeishuClientProtocol
from app.services.feishu_oauth import FeishuOAuthProtocol, FeishuReauthorizationRequired


@dataclass(frozen=True)
class ReportItem:
    image_id: str
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
    rules: tuple[RuleSummary, ...]
    items: tuple[ReportItem, ...]
    snapshot_hash: str


class ReportBuilder:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        feishu: FeishuClientProtocol,
    ) -> None:
        self.sessions = sessions
        self.feishu = feishu

    def project(self, task_id: str) -> ReportProjection:
        with self.sessions() as session:
            repo = TaskRepository(session)
            task = repo.get_task(task_id)
            images = repo.list_images(task_id)
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
            FeishuApiError,
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
        if export.document_id is None:
            folder_token = self._folder_token()
            timestamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M")
            title = f"{projection.task_name} 巡检报告 {timestamp}"
            document = await self.feishu.create_document(title, folder_token, export.id)
            self._save_document(export.id, document.id, document.url)
            export = self._get_export(export.id)
        assert export.document_id is not None

        await self._write_blocks(
            export,
            "overview",
            self._overview_blocks(projection),
        )
        for item in projection.items:
            await self._write_blocks(
                export,
                f"detail:{item.image_id}",
                self._item_blocks(item),
            )
            await self._write_image_item(export, item)
        await self._write_blocks(export, "trace", self._trace_blocks(projection))
        self._mark_succeeded(export.id)

    async def _write_blocks(
        self,
        export: ReportExport,
        operation_key: str,
        blocks: list[dict[str, Any]],
    ) -> None:
        if self._operation_completed(export.id, operation_key):
            return
        assert export.document_id is not None
        await self.feishu.create_blocks(
            export.document_id,
            export.document_id,
            blocks,
            operation_key,
        )
        self._mark_operation_completed(export.id, operation_key)

    async def _write_image_item(self, export: ReportExport, item: ReportItem) -> None:
        operation_key = f"image:{item.image_id}"
        if self._operation_completed(export.id, operation_key):
            return
        assert export.document_id is not None
        block_id = self._remote_block(export.id, operation_key)
        if block_id is None:
            block = await self.feishu.create_image_block(
                export.document_id,
                export.document_id,
                operation_key,
            )
            block_id = block.id
            self._save_remote_block(export.id, operation_key, block_id)
        content = Path(item.thumbnail_path).read_bytes()
        await self.feishu.upload_docx_image(block_id, item.filename, content)
        self._mark_operation_completed(export.id, operation_key)

    @staticmethod
    def _text_block(content: str, heading: bool = False) -> dict[str, Any]:
        key = "heading1" if heading else "text"
        return {
            "block_type": 3 if heading else 2,
            key: {
                "elements": [
                    {
                        "text_run": {
                            "content": content,
                            "text_element_style": {},
                        }
                    }
                ],
                "style": {},
            },
        }

    def _overview_blocks(self, projection: ReportProjection) -> list[dict[str, Any]]:
        count_text = "｜".join(
            [
                f"合格 {projection.counts['pass']}",
                f"不合格 {projection.counts['fail']}",
                f"无法判断 {projection.counts['unverifiable']}",
                f"执行失败 {projection.counts['failed']}",
            ]
        )
        issue_text = "；".join(
            f"{rule.rule_name}：{rule.failures}" for rule in projection.rules
        ) or "无可见违规项"
        return [
            self._text_block("AI 生成结果，仅供人工复核；系统只判断图片中清晰可见的内容。"),
            self._text_block("任务信息", heading=True),
            self._text_block(
                f"模板：{projection.template_name} v{projection.template_version}\n"
                f"模型/Endpoint：{projection.model}\n图片数量：{len(projection.items)}"
            ),
            self._text_block("汇总统计", heading=True),
            self._text_block(count_text),
            self._text_block("问题汇总", heading=True),
            self._text_block(issue_text),
        ]

    def _item_blocks(self, item: ReportItem) -> list[dict[str, Any]]:
        blocks = [self._text_block(item.filename, heading=True)]
        if item.result is None:
            blocks.append(self._text_block(f"执行失败：{item.execution_status}"))
            return blocks
        result = item.result
        blocks.append(
            self._text_block(
                f"结论：{result.verdict.value}\n摘要：{result.summary}"
            )
        )
        for finding in result.findings:
            blocks.append(
                self._text_block(
                    f"{finding.rule_name} [{finding.status.value}]：{finding.observation}\n"
                    f"视觉证据：{finding.visual_evidence}"
                )
            )
        return blocks

    def _trace_blocks(self, projection: ReportProjection) -> list[dict[str, Any]]:
        return [
            self._text_block("追溯信息", heading=True),
            self._text_block(
                f"任务 ID：{projection.task_id}\n"
                f"模板快照哈希：{projection.snapshot_hash}\nSchema 版本：1"
            ),
        ]

    def _folder_token(self) -> str:
        with self.sessions() as session:
            settings = SettingsRepository(session).get("feishu") or {}
            return str(settings.get("folder_token", ""))

    def _get_export(self, export_id: str) -> ReportExport:
        with self.sessions() as session:
            return TaskRepository(session).get_export(export_id)

    def _save_document(self, export_id: str, document_id: str, document_url: str) -> None:
        with self.sessions() as session:
            TaskRepository(session).save_export_document(export_id, document_id, document_url)
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
            export = TaskRepository(session).get_export(export_id)
            return export.remote_blocks_json.get(operation_key)

    def _save_remote_block(self, export_id: str, operation_key: str, block_id: str) -> None:
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
        oauth: FeishuOAuthProtocol,
        events: TaskEventBroker,
    ) -> None:
        self.builder = builder
        self.oauth = oauth
        self.events = events
        self._active: dict[str, asyncio.Task[None]] = {}

    def is_ready(self) -> bool:
        try:
            configured, connected, _expires_at = self.oauth.connection_status()
        except RuntimeError:
            return False
        return configured and connected

    async def enqueue_auto(self, task_id: str) -> str | None:
        if not self.is_ready():
            return None
        return await self.enqueue(task_id)

    async def enqueue(
        self,
        task_id: str,
        include_failures: bool = False,
        new_version: bool = False,
    ) -> str:
        if not self.is_ready():
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
