from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.states import ImageStatus, ReportStatus, TaskStatus, require_task_transition
from app.models import (
    AppSetting,
    InferenceAttempt,
    InferenceResultRecord,
    InspectionTask,
    PromptTemplate,
    PromptTemplateVersion,
    ReportExport,
    TaskImage,
)


class SettingsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, key: str) -> Any | None:
        setting = self.session.get(AppSetting, key)
        return None if setting is None else setting.value_json

    def set(self, key: str, value: Any) -> None:
        setting = self.session.get(AppSetting, key)
        if setting is None:
            self.session.add(AppSetting(key=key, value_json=value))
        else:
            setting.value_json = value


class TemplateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, name: str, description: str) -> PromptTemplate:
        template = PromptTemplate(name=name, description=description)
        self.session.add(template)
        self.session.flush()
        return template

    def add_version(
        self,
        template_id: str,
        rules: list[dict[str, Any]],
        model_params: dict[str, Any],
        system_prompt: str = "",
        task_prompt: str = "",
    ) -> PromptTemplateVersion:
        next_version = self.session.scalar(
            select(func.coalesce(func.max(PromptTemplateVersion.version), 0) + 1).where(
                PromptTemplateVersion.template_id == template_id
            )
        )
        version = PromptTemplateVersion(
            template_id=template_id,
            version=int(next_version or 1),
            rules_json=rules,
            model_params_json=model_params,
            system_prompt=system_prompt,
            task_prompt=task_prompt,
        )
        self.session.add(version)
        self.session.flush()
        template = self.session.get(PromptTemplate, template_id)
        if template is None:
            raise LookupError(template_id)
        template.current_version_id = version.id
        return version

    def get_version(self, version_id: str) -> PromptTemplateVersion:
        version = self.session.get(PromptTemplateVersion, version_id)
        if version is None:
            raise LookupError(version_id)
        return version

    def get(self, template_id: str) -> PromptTemplate:
        template = self.session.get(PromptTemplate, template_id)
        if template is None:
            raise LookupError(template_id)
        return template

    def get_by_name(self, name: str) -> PromptTemplate | None:
        return self.session.scalar(select(PromptTemplate).where(PromptTemplate.name == name))

    def count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(PromptTemplate)) or 0)

    def list_templates(self) -> list[PromptTemplate]:
        return list(self.session.scalars(select(PromptTemplate).order_by(PromptTemplate.created_at)))

    def get_current_version(self, template_id: str) -> PromptTemplateVersion:
        template = self.get(template_id)
        if template.current_version_id is None:
            raise LookupError(f"template {template_id} has no current version")
        return self.get_version(template.current_version_id)

    def get_version_number(self, template_id: str, version: int) -> PromptTemplateVersion:
        record = self.session.scalar(
            select(PromptTemplateVersion).where(
                PromptTemplateVersion.template_id == template_id,
                PromptTemplateVersion.version == version,
            )
        )
        if record is None:
            raise LookupError(f"{template_id}:v{version}")
        return record


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_task(
        self,
        name: str,
        template_snapshot: dict[str, Any],
        model_snapshot: dict[str, Any],
    ) -> InspectionTask:
        task = InspectionTask(
            name=name,
            template_snapshot_json=template_snapshot,
            model_snapshot_json=model_snapshot,
        )
        self.session.add(task)
        self.session.flush()
        return task

    def add_image(
        self,
        task_id: str,
        position: int,
        original_name: str,
        sha256: str,
        original_path: str,
        inference_path: str,
        thumbnail_path: str,
    ) -> TaskImage:
        self.get_task(task_id)
        image = TaskImage(
            task_id=task_id,
            position=position,
            original_name=original_name,
            sha256=sha256,
            original_path=original_path,
            inference_path=inference_path,
            thumbnail_path=thumbnail_path,
        )
        self.session.add(image)
        self.session.flush()
        return image

    def get_task(self, task_id: str) -> InspectionTask:
        task = self.session.get(InspectionTask, task_id)
        if task is None:
            raise LookupError(task_id)
        return task

    def get_image(self, image_id: str) -> TaskImage:
        image = self.session.get(TaskImage, image_id)
        if image is None:
            raise LookupError(image_id)
        return image

    def set_task_status(self, task_id: str, status: TaskStatus) -> InspectionTask:
        task = self.get_task(task_id)
        require_task_transition(TaskStatus(task.status), status)
        task.status = status.value
        return task

    def set_image_status(self, image_id: str, status: ImageStatus) -> TaskImage:
        image = self.get_image(image_id)
        image.status = status.value
        return image

    def claim_next_image(self, task_id: str) -> TaskImage | None:
        image = self.session.scalar(
            select(TaskImage)
            .where(
                TaskImage.task_id == task_id,
                TaskImage.status == ImageStatus.QUEUED.value,
            )
            .order_by(TaskImage.position)
            .limit(1)
        )
        if image is None:
            return None
        image.status = ImageStatus.RUNNING.value
        image.attempt_count += 1
        self.session.flush()
        return image

    def save_result(
        self,
        image_id: str,
        result: dict[str, Any],
        raw_text_redacted: str,
        correction: dict[str, Any] | None = None,
    ) -> InferenceResultRecord:
        self.get_image(image_id)
        self.session.execute(
            update(InferenceResultRecord)
            .where(
                InferenceResultRecord.image_id == image_id,
                InferenceResultRecord.is_current.is_(True),
            )
            .values(is_current=False)
        )
        record = InferenceResultRecord(
            image_id=image_id,
            schema_version=int(result.get("schema_version", 1)),
            result_json=result,
            raw_text_redacted=raw_text_redacted,
            correction_json=correction,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def record_attempt(
        self,
        image_id: str,
        status: str,
        status_code: int | None = None,
        error_code: str | None = None,
        duration_ms: int | None = None,
        usage: dict[str, Any] | None = None,
    ) -> InferenceAttempt:
        image = self.get_image(image_id)
        attempt = InferenceAttempt(
            image_id=image_id,
            attempt_number=image.attempt_count,
            status=status,
            status_code=status_code,
            error_code=error_code,
            duration_ms=duration_ms,
            usage_json=usage,
        )
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def get_or_create_active_export(self, task_id: str) -> ReportExport:
        self.get_task(task_id)
        export = self.session.scalar(
            select(ReportExport).where(
                ReportExport.task_id == task_id,
                ReportExport.is_active.is_(True),
            )
        )
        if export is None:
            export = ReportExport(task_id=task_id, status=ReportStatus.PENDING.value)
            self.session.add(export)
            self.session.flush()
        return export

    def recover_interrupted_work(self) -> int:
        running_tasks = self.session.scalars(
            select(InspectionTask).where(InspectionTask.status == TaskStatus.RUNNING.value)
        ).all()
        count = 0
        for task in running_tasks:
            task.status = TaskStatus.PAUSED.value
            images = self.session.scalars(
                select(TaskImage).where(
                    TaskImage.task_id == task.id,
                    TaskImage.status == ImageStatus.RUNNING.value,
                )
            ).all()
            for image in images:
                image.status = ImageStatus.QUEUED.value
                count += 1
        return count
