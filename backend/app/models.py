from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PromptTemplateVersion(Base):
    __tablename__ = "prompt_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version"),)

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    template_id: Mapped[str] = mapped_column(ForeignKey("prompt_templates.id"))
    version: Mapped[int] = mapped_column(Integer)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    task_prompt: Mapped[str] = mapped_column(Text, default="")
    rules_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    model_params_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InspectionTask(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    template_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    model_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskImage(Base):
    __tablename__ = "task_images"
    __table_args__ = (
        UniqueConstraint("task_id", "position"),
        UniqueConstraint("task_id", "sha256"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer)
    original_name: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    original_path: Mapped[str] = mapped_column(Text)
    inference_path: Mapped[str] = mapped_column(Text)
    thumbnail_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class InferenceAttempt(Base):
    __tablename__ = "inference_attempts"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    image_id: Mapped[str] = mapped_column(
        ForeignKey("task_images.id", ondelete="CASCADE"),
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    status_code: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InferenceResultRecord(Base):
    __tablename__ = "inference_results"
    __table_args__ = (
        Index(
            "uq_current_result_per_image",
            "image_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    image_id: Mapped[str] = mapped_column(
        ForeignKey("task_images.id", ondelete="CASCADE"),
        index=True,
    )
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    raw_text_redacted: Mapped[str] = mapped_column(Text)
    correction_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReportExport(Base):
    __tablename__ = "report_exports"
    __table_args__ = (
        Index(
            "uq_active_report_per_task",
            "task_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    document_id: Mapped[str | None] = mapped_column(String(100))
    document_url: Mapped[str | None] = mapped_column(Text)
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    completed_operations_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    remote_blocks_json: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
