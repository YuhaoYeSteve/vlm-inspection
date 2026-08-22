# Seed VLM Inspection MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local single-user web application that runs one-image-per-request Seed VLM inspections, validates structured results, survives restarts, and creates image-rich Feishu reports.

**Architecture:** A React/TypeScript SPA talks to a single-process FastAPI service bound to `127.0.0.1`. FastAPI owns a SQLite-backed queue, local image derivatives, Ark and Feishu adapters, and system-keychain secrets; SSE carries progress while REST remains the source of truth.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, SQLite, Pillow, keyring, Volcengine Ark Python SDK, httpx, pytest; React 19, TypeScript, Vite, TanStack Query, React Router, Vitest, Testing Library, Playwright.

**Reference:** [`docs/superpowers/specs/2026-08-22-vlm-inspection-system-design.md`](../specs/2026-08-22-vlm-inspection-system-design.md)

---

## File map

### Backend

- `backend/pyproject.toml` — Python package metadata, runtime dependencies, and test/lint configuration.
- `backend/app/main.py` — application factory, routers, lifecycle, and static frontend mounting.
- `backend/app/core/config.py` — environment configuration and `~/.vlm-inspector` paths.
- `backend/app/core/db.py` — SQLAlchemy engine/session construction.
- `backend/app/core/secrets.py` — keychain-only secret storage.
- `backend/app/domain/results.py` — fixed inspection result schema and deterministic verdict normalization.
- `backend/app/domain/states.py` — task/image/report enums and legal transitions.
- `backend/app/models.py` — SQLite ORM entities and constraints.
- `backend/app/repositories.py` — transaction-scoped persistence operations.
- `backend/app/schemas.py` — REST request and response models.
- `backend/app/api/settings.py` — Ark and Feishu settings endpoints.
- `backend/app/api/templates.py` — template versioning and single-image test endpoints.
- `backend/app/api/tasks.py` — task, image upload, lifecycle, retry, report, and SSE endpoints.
- `backend/app/api/feishu_auth.py` — OAuth start, callback, status, and disconnect endpoints.
- `backend/app/services/images.py` — validation, EXIF correction, derivatives, hashing, and duplicate detection.
- `backend/app/services/prompts.py` — system policy, starter templates, and final prompt assembly.
- `backend/app/services/ark.py` — Responses and Chat Completions adapters plus structured-output repair.
- `backend/app/services/retry.py` — retry classification and jittered exponential backoff.
- `backend/app/services/events.py` — per-task SSE event broker.
- `backend/app/services/runner.py` — persistent local queue and task state orchestration.
- `backend/app/services/feishu_oauth.py` — signed OAuth state, token exchange, refresh, and keychain persistence.
- `backend/app/services/feishu_client.py` — Docx block and Drive media operations with rate limiting.
- `backend/app/services/reports.py` — report projection, resumable export, and idempotency.
- `backend/alembic.ini`, `backend/alembic/` — database migrations.
- `backend/tests/` — unit, API, contract, persistence, and integration tests.

### Frontend and operations

- `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig*.json` — SPA toolchain.
- `frontend/src/api/client.ts` — typed REST helpers and SSE connection.
- `frontend/src/api/types.ts` — frontend view of backend contracts.
- `frontend/src/app/App.tsx`, `frontend/src/app/router.tsx` — shell, navigation, and routes.
- `frontend/src/test/setup.ts` — Vitest DOM matcher setup.
- `frontend/src/features/settings/SettingsPage.tsx` — masked Ark/Feishu configuration and connection tests.
- `frontend/src/features/templates/TemplatesPage.tsx` — versioned templates and single-image tests.
- `frontend/src/features/tasks/TaskPage.tsx` — upload, controls, progress, gallery, detail, filters, and retries.
- `frontend/src/features/history/HistoryPage.tsx` — task history and Feishu report links.
- `frontend/src/styles.css` — approved warm-neutral visual system and responsive workspace layout.
- `frontend/e2e/` — Playwright end-to-end flows.
- `scripts/dev.sh`, `scripts/start.sh` — development and production launchers.
- `README.md`, `.env.example` — setup, Feishu application permissions, operation, and troubleshooting.

## Task 1: Bootstrap the FastAPI package and health check

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/tests/test_health.py`

- [ ] **Step 1: Add the backend package definition and failing health test**

```toml
# backend/pyproject.toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "vlm-inspector"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "alembic>=1.14,<2",
  "fastapi>=0.115,<1",
  "httpx>=0.28,<1",
  "keyring>=25,<26",
  "pillow>=11,<12",
  "pydantic-settings>=2.7,<3",
  "python-multipart>=0.0.20,<1",
  "sse-starlette>=2.2,<3",
  "sqlalchemy>=2.0,<3",
  "uvicorn[standard]>=0.34,<1",
  "volcengine-python-sdk[ark]>=5.0.36,<6"
]

[project.optional-dependencies]
dev = [
  "mypy>=1.14,<2",
  "pytest>=8.3,<9",
  "pytest-asyncio>=0.25,<1",
  "ruff>=0.9,<1"
]

[tool.hatch.build.targets.wheel]
packages = ["app"]

[tool.pytest.ini_options]
addopts = "-q"
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.mypy]
python_version = "3.11"
plugins = ["pydantic.mypy"]
strict = true
```

```python
# backend/tests/test_health.py
from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_ready() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
```

- [ ] **Step 2: Create the virtual environment, install dependencies, and verify the test fails**

Run:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install --upgrade pip
backend/.venv/bin/python -m pip install -e 'backend[dev]'
backend/.venv/bin/pytest backend/tests/test_health.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app'` or `No module named 'app.main'`.

- [ ] **Step 3: Add the minimal application factory**

```python
# backend/app/__init__.py
"""Local Seed VLM inspection service."""
```

```python
# backend/app/main.py
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Seed VLM Inspector", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ready"}

    return app


app = create_app()
```

- [ ] **Step 4: Run backend tests and lint**

Run:

```bash
backend/.venv/bin/pytest backend/tests/test_health.py -v
backend/.venv/bin/ruff check backend/app backend/tests
```

Expected: one passing test and no Ruff errors.

- [ ] **Step 5: Commit the backend foundation**

```bash
git add backend
git commit -m "chore: bootstrap FastAPI backend"
```

## Task 2: Define result contracts and state machines

**Files:**
- Create: `backend/app/domain/__init__.py`
- Create: `backend/app/domain/results.py`
- Create: `backend/app/domain/states.py`
- Create: `backend/tests/domain/test_results.py`
- Create: `backend/tests/domain/test_states.py`

- [ ] **Step 1: Write failing domain tests**

```python
# backend/tests/domain/test_results.py
import pytest
from pydantic import ValidationError

from app.domain.results import Finding, FindingStatus, InspectionResult, Verdict


def test_result_recomputes_fail_from_findings() -> None:
    result = InspectionResult(
        image_valid=True,
        verdict=Verdict.PASS,
        summary="工牌缺失",
        overall_confidence=0.92,
        findings=[
            Finding(
                rule_id="badge",
                rule_name="工牌佩戴",
                status=FindingStatus.FAIL,
                observation="左胸未见工牌",
                visual_evidence="左胸区域无遮挡",
                confidence=0.92,
            )
        ],
    ).normalized()
    assert result.verdict is Verdict.FAIL


def test_invalid_image_requires_reason() -> None:
    with pytest.raises(ValidationError):
        InspectionResult(
            image_valid=False,
            verdict=Verdict.UNVERIFIABLE,
            summary="无法检查",
            overall_confidence=1.0,
            findings=[],
        )
```

```python
# backend/tests/domain/test_states.py
import pytest

from app.domain.states import InvalidTransition, TaskStatus, require_task_transition


def test_running_can_pause() -> None:
    require_task_transition(TaskStatus.RUNNING, TaskStatus.PAUSED)


def test_completed_cannot_restart() -> None:
    with pytest.raises(InvalidTransition):
        require_task_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING)
```

- [ ] **Step 2: Run the tests and confirm missing modules**

Run: `backend/.venv/bin/pytest backend/tests/domain -v`

Expected: FAIL because `app.domain.results` and `app.domain.states` do not exist.

- [ ] **Step 3: Implement the fixed result schema and deterministic normalization**

```python
# backend/app/domain/__init__.py
"""Pure domain contracts shared by APIs and services."""
```

```python
# backend/app/domain/results.py
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNVERIFIABLE = "unverifiable"


class FindingStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_VISIBLE = "not_visible"


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    rule_name: str = Field(min_length=1)
    status: FindingStatus
    observation: str = Field(min_length=1)
    visual_evidence: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class InspectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    image_valid: bool
    unverifiable_reason: str | None = None
    verdict: Verdict
    summary: str = Field(min_length=1)
    overall_confidence: float = Field(ge=0, le=1)
    findings: list[Finding]

    @model_validator(mode="after")
    def validate_visibility(self) -> Self:
        if not self.image_valid and not self.unverifiable_reason:
            raise ValueError("invalid images require unverifiable_reason")
        return self

    def normalized(self) -> Self:
        visible = [item for item in self.findings if item.status is not FindingStatus.NOT_VISIBLE]
        if not self.image_valid or not visible:
            verdict = Verdict.UNVERIFIABLE
        elif any(item.status is FindingStatus.FAIL for item in visible):
            verdict = Verdict.FAIL
        else:
            verdict = Verdict.PASS
        return self.model_copy(update={"verdict": verdict})
```

- [ ] **Step 4: Implement legal task transitions**

```python
# backend/app/domain/states.py
from enum import StrEnum


class TaskStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    REVIEW_REQUIRED = "review_required"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    CANCELED = "canceled"


class ImageStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class ReportStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class InvalidTransition(ValueError):
    pass


_TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.DRAFT: {TaskStatus.QUEUED, TaskStatus.CANCELED},
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.CANCELED},
    TaskStatus.RUNNING: {
        TaskStatus.PAUSED,
        TaskStatus.REVIEW_REQUIRED,
        TaskStatus.EXPORTING,
        TaskStatus.CANCELED,
    },
    TaskStatus.PAUSED: {TaskStatus.QUEUED, TaskStatus.CANCELED},
    TaskStatus.REVIEW_REQUIRED: {TaskStatus.QUEUED, TaskStatus.EXPORTING, TaskStatus.CANCELED},
    TaskStatus.EXPORTING: {TaskStatus.COMPLETED, TaskStatus.REVIEW_REQUIRED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.CANCELED: set(),
}


def require_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in _TASK_TRANSITIONS[current]:
        raise InvalidTransition(f"illegal task transition: {current} -> {target}")
```

- [ ] **Step 5: Run domain tests and type checks**

Run:

```bash
backend/.venv/bin/pytest backend/tests/domain -v
backend/.venv/bin/mypy backend/app/domain
```

Expected: four passing tests and no mypy errors.

- [ ] **Step 6: Commit the domain layer**

```bash
git add backend/app/domain backend/tests/domain
git commit -m "feat: define inspection contracts and task states"
```

## Task 3: Add configuration paths and template persistence

**Files:**
- Create: `backend/app/core/__init__.py`
- Create: `backend/app/core/config.py`
- Create: `backend/app/core/db.py`
- Create: `backend/app/models.py`
- Create: `backend/app/repositories.py`
- Create: `backend/tests/persistence/test_templates.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/versions/20260822_01_initial.py`

- [ ] **Step 1: Write a failing template-version persistence test**

```python
# backend/tests/persistence/test_templates.py
from app.core.db import Base, make_engine, make_session_factory
from app.repositories import TemplateRepository


def test_template_update_creates_immutable_version(tmp_path) -> None:
    engine = make_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions() as session:
        repo = TemplateRepository(session)
        template = repo.create("通用仪容仪表", "员工可见项目巡检")
        first = repo.add_version(template.id, [{"rule_id": "badge", "name": "工牌"}], {})
        second = repo.add_version(template.id, [{"rule_id": "uniform", "name": "制服"}], {})
        session.commit()
        assert first.version == 1
        assert second.version == 2
        assert repo.get_version(first.id).rules_json[0]["rule_id"] == "badge"
```

- [ ] **Step 2: Run the persistence test and verify it fails**

Run: `backend/.venv/bin/pytest backend/tests/persistence/test_templates.py -v`

Expected: FAIL because database modules do not exist.

- [ ] **Step 3: Implement paths and database construction**

```python
# backend/app/core/__init__.py
"""Infrastructure configuration."""
```

```python
# backend/app/core/config.py
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VLM_", env_file=".env")

    data_dir: Path = Field(default_factory=lambda: Path.home() / ".vlm-inspector")
    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def database_path(self) -> Path:
        return self.data_dir / "app.db"

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.data_dir / "images",
            self.data_dir / "logs",
        ):
            path.mkdir(parents=True, exist_ok=True)
```

```python
# backend/app/core/db.py
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enable_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
```

- [ ] **Step 4: Implement settings and versioned-template models and repository**

```python
# backend/app/models.py
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
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
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PromptTemplateVersion(Base):
    __tablename__ = "prompt_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    template_id: Mapped[str] = mapped_column(ForeignKey("prompt_templates.id"))
    version: Mapped[int] = mapped_column(Integer)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    task_prompt: Mapped[str] = mapped_column(Text, default="")
    rules_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    model_params_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

```python
# backend/app/repositories.py
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PromptTemplate, PromptTemplateVersion


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
```

- [ ] **Step 5: Configure Alembic and create the exact initial migration**

Initialize with `cd backend && .venv/bin/alembic init alembic`, then replace generated `env.py` so `target_metadata = Base.metadata` and imports `app.models`. Generate the migration:

```bash
cd backend
.venv/bin/alembic revision --autogenerate -m "initial schema"
.venv/bin/alembic upgrade head
```

Rename the generated revision file to `20260822_01_initial.py` while preserving its generated `revision` and `down_revision` values. Expected: `app_settings`, `prompt_templates`, and `prompt_template_versions` exist in `~/.vlm-inspector/app.db` when `VLM_DATA_DIR` is not overridden.

- [ ] **Step 6: Run persistence tests**

Run: `backend/.venv/bin/pytest backend/tests/persistence/test_templates.py -v`

Expected: one passing test.

- [ ] **Step 7: Commit database foundations**

```bash
git add backend/app/core backend/app/models.py backend/app/repositories.py backend/alembic.ini backend/alembic backend/tests/persistence
git commit -m "feat: persist versioned prompt templates"
```

## Task 4: Persist tasks, images, attempts, results, and reports

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/repositories.py`
- Create: `backend/alembic/versions/20260822_02_task_entities.py`
- Create: `backend/tests/persistence/test_tasks.py`

- [ ] **Step 1: Write failing tests for immutable image/result binding and restart recovery**

```python
# backend/tests/persistence/test_tasks.py
from app.core.db import Base, make_engine, make_session_factory
from app.domain.states import ImageStatus, TaskStatus
from app.repositories import TaskRepository


def test_result_binds_to_image_id_and_recovers_running_items(tmp_path) -> None:
    engine = make_engine(tmp_path / "tasks.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions() as session:
        repo = TaskRepository(session)
        task = repo.create_task("夜班巡检", {"template_version": 1}, {"model": "seed"})
        image = repo.add_image(task.id, 0, "a.png", "abc", "o.png", "i.jpg", "t.jpg")
        repo.set_task_status(task.id, TaskStatus.QUEUED)
        repo.set_task_status(task.id, TaskStatus.RUNNING)
        repo.set_image_status(image.id, ImageStatus.RUNNING)
        session.commit()
    with sessions() as session:
        repo = TaskRepository(session)
        recovered = repo.recover_interrupted_work()
        session.commit()
        assert recovered == 1
        assert repo.get_task(task.id).status == TaskStatus.PAUSED
        assert repo.get_image(image.id).status == ImageStatus.QUEUED
```

- [ ] **Step 2: Run the test and confirm missing repository methods**

Run: `backend/.venv/bin/pytest backend/tests/persistence/test_tasks.py -v`

Expected: FAIL because task entities and repository methods do not exist.

- [ ] **Step 3: Add task entities and uniqueness constraints**

Append these entities to `backend/app/models.py`:

```python
class InspectionTask(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    template_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    model_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskImage(Base):
    __tablename__ = "task_images"
    __table_args__ = (UniqueConstraint("task_id", "position"), UniqueConstraint("task_id", "sha256"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
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
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    image_id: Mapped[str] = mapped_column(ForeignKey("task_images.id", ondelete="CASCADE"), index=True)
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
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    image_id: Mapped[str] = mapped_column(ForeignKey("task_images.id", ondelete="CASCADE"), index=True)
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
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    document_id: Mapped[str | None] = mapped_column(String(100))
    document_url: Mapped[str | None] = mapped_column(Text)
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    completed_operations_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    remote_blocks_json: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

- [ ] **Step 4: Add transactional task repository methods**

Extend `backend/app/repositories.py` with `TaskRepository` methods named exactly as used by the test plus `claim_next_image`, `save_result`, `record_attempt`, and `get_or_create_active_export`. `claim_next_image` must select the first queued image ordered by `position`, change it to `running`, increment `attempt_count`, flush, and return it within the caller's transaction. `save_result` must mark any current result for the same image as non-current before inserting the new record.

Use enum `.value` for storage and reconstruct enum values on repository returns. Implement recovery with one transaction:

```python
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
```

- [ ] **Step 5: Generate and apply the task-entity migration**

Run:

```bash
cd backend
.venv/bin/alembic revision --autogenerate -m "add task entities"
.venv/bin/alembic upgrade head
```

Rename the generated revision to `20260822_02_task_entities.py`. Expected: five new tables and their unique constraints appear.

- [ ] **Step 6: Run persistence tests**

Run: `backend/.venv/bin/pytest backend/tests/persistence -v`

Expected: all persistence tests pass.

- [ ] **Step 7: Commit task persistence**

```bash
git add backend/app/models.py backend/app/repositories.py backend/alembic backend/tests/persistence
git commit -m "feat: persist inspection tasks and results"
```

## Task 5: Store secrets safely and expose masked settings APIs

**Files:**
- Create: `backend/app/core/secrets.py`
- Create: `backend/app/schemas.py`
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/settings.py`
- Create: `backend/app/dependencies.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/api/test_settings.py`

- [ ] **Step 1: Write a failing API test proving the API Key never returns or enters SQLite**

```python
# backend/tests/api/test_settings.py
from fastapi.testclient import TestClient

from app.main import create_app


def test_save_ark_settings_masks_secret(app_harness) -> None:
    client: TestClient = app_harness.client
    response = client.put(
        "/api/settings/ark",
        json={
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "protocol": "responses",
            "model": "doubao-seed-test",
            "api_key": "ark-secret-value",
            "concurrency": 3,
            "timeout_seconds": 120,
            "max_retries": 2,
            "thinking": "disabled",
        },
    )
    assert response.status_code == 200
    assert response.json()["api_key_configured"] is True
    assert "ark-secret-value" not in response.text
    assert "ark-secret-value" not in app_harness.database_path.read_bytes().decode(
        "utf-8", errors="ignore"
    )
    assert app_harness.secrets.get("ark_api_key") == "ark-secret-value"
```

Add `backend/tests/conftest.py` with an `app_harness` fixture that creates a temporary SQLite database, an in-memory `FakeSecretStore`, and `create_app` dependency overrides.

- [ ] **Step 2: Run the test and confirm the endpoint is absent**

Run: `backend/.venv/bin/pytest backend/tests/api/test_settings.py -v`

Expected: FAIL with HTTP 404 or missing dependency objects.

- [ ] **Step 3: Implement keychain-only secret storage**

```python
# backend/app/core/secrets.py
from typing import Protocol

import keyring
from keyring.errors import KeyringError


class SecretStoreProtocol(Protocol):
    def get(self, key: str) -> str | None:
        raise NotImplementedError

    def set(self, key: str, value: str) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class KeyringSecretStore:
    def __init__(self, service: str = "vlm-inspector") -> None:
        self.service = service

    def get(self, key: str) -> str | None:
        try:
            return keyring.get_password(self.service, key)
        except KeyringError as exc:
            raise RuntimeError("system keychain is unavailable") from exc

    def set(self, key: str, value: str) -> None:
        try:
            keyring.set_password(self.service, key, value)
        except KeyringError as exc:
            raise RuntimeError("system keychain is unavailable") from exc

    def delete(self, key: str) -> None:
        try:
            keyring.delete_password(self.service, key)
        except keyring.errors.PasswordDeleteError:
            return
        except KeyringError as exc:
            raise RuntimeError("system keychain is unavailable") from exc
```

- [ ] **Step 4: Implement settings schemas, repository access, and routes**

Define `ArkSettingsWrite`, `ArkSettingsRead`, `FeishuSettingsWrite`, and `FeishuSettingsRead` in `schemas.py`. Enforce `concurrency` 1–5, positive timeout, retries 0–5, protocol enum `responses|chat`, and thinking enum `disabled|auto|enabled`.

```python
class ArkSettingsWrite(BaseModel):
    base_url: AnyHttpUrl
    protocol: Literal["responses", "chat"]
    model: str = Field(min_length=1)
    api_key: SecretStr | None = None
    concurrency: int = Field(default=3, ge=1, le=5)
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)
    thinking: Literal["disabled", "auto", "enabled"] = "disabled"


class ArkSettingsRead(BaseModel):
    base_url: str
    protocol: Literal["responses", "chat"]
    model: str
    concurrency: int
    timeout_seconds: int
    max_retries: int
    thinking: Literal["disabled", "auto", "enabled"]
    api_key_configured: bool
```

Persist only non-secret values under `AppSetting` keys `ark` and `feishu`. Store `ark_api_key` and `feishu_app_secret` in `SecretStoreProtocol`. Return only `api_key_configured` and `app_secret_configured` booleans. Convert keychain failures to HTTP 503 with code `keychain_unavailable`.

```python
@router.put("/ark", response_model=ArkSettingsRead)
def save_ark_settings(
    body: ArkSettingsWrite,
    session: Session = Depends(get_session),
    secrets: SecretStoreProtocol = Depends(get_secret_store),
) -> ArkSettingsRead:
    if body.api_key is not None:
        secrets.set("ark_api_key", body.api_key.get_secret_value())
    public = body.model_dump(exclude={"api_key"}, mode="json")
    SettingsRepository(session).set("ark", public)
    session.commit()
    return ArkSettingsRead(**public, api_key_configured=secrets.get("ark_api_key") is not None)
```

Wire dependencies in `dependencies.py`, include the router in `create_app`, and make `create_app` accept optional engine and secret-store overrides for tests.

- [ ] **Step 5: Run settings tests and scan for leaked test secrets**

Run:

```bash
backend/.venv/bin/pytest backend/tests/api/test_settings.py -v
rg -n "ark-secret-value" backend --glob '!tests/api/test_settings.py'
```

Expected: the test passes and `rg` returns no matches.

- [ ] **Step 6: Commit safe settings storage**

```bash
git add backend/app backend/tests
git commit -m "feat: add keychain-backed connection settings"
```

## Task 6: Import, validate, normalize, and deduplicate images

**Files:**
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/images.py`
- Create: `backend/tests/services/test_images.py`

- [ ] **Step 1: Write failing image-ingestion tests**

```python
# backend/tests/services/test_images.py
from io import BytesIO

import pytest
from PIL import Image

from app.services.images import DuplicateImage, ImageIngestor, UnsupportedImage


def png_bytes(color: str = "red") -> bytes:
    output = BytesIO()
    Image.new("RGB", (3000, 1000), color).save(output, format="PNG")
    return output.getvalue()


def test_ingest_creates_bounded_derivatives(tmp_path) -> None:
    ingestor = ImageIngestor(tmp_path)
    item = ingestor.ingest("task-1", "sample.png", png_bytes())
    with Image.open(item.inference_path) as image:
        assert max(image.size) == 2048
    with Image.open(item.thumbnail_path) as image:
        assert max(image.size) == 1600


def test_ingest_rejects_duplicate_hash(tmp_path) -> None:
    ingestor = ImageIngestor(tmp_path)
    ingestor.ingest("task-1", "a.png", png_bytes())
    with pytest.raises(DuplicateImage):
        ingestor.ingest("task-1", "b.png", png_bytes())


def test_ingest_rejects_non_image(tmp_path) -> None:
    with pytest.raises(UnsupportedImage):
        ImageIngestor(tmp_path).ingest("task-1", "bad.txt", b"not an image")
```

- [ ] **Step 2: Run tests and confirm missing service**

Run: `backend/.venv/bin/pytest backend/tests/services/test_images.py -v`

Expected: FAIL because `app.services.images` does not exist.

- [ ] **Step 3: Implement deterministic image ingestion**

```python
# backend/app/services/__init__.py
"""External adapters and orchestration services."""
```

Implement `ImageIngestor` with these exact rules in `images.py`:

- sanitize the basename with `Path(filename).name`;
- reject empty files and files over 20 MiB;
- accept only formats reported by Pillow as JPEG, PNG, or WEBP;
- compute SHA-256 over original bytes;
- maintain `<task>/hashes.json` under the image root and atomically reject a repeated hash;
- save originals under `original/<uuid>-<safe-name>`;
- apply `ImageOps.exif_transpose`, flatten alpha over white, and convert to RGB;
- use `thumbnail((2048, 2048), Resampling.LANCZOS)` and JPEG quality 90 for inference;
- use `thumbnail((1600, 1600), Resampling.LANCZOS)` and JPEG quality 82 for report thumbnails;
- write to a temporary sibling and `Path.replace` so interrupted writes never leave a valid-looking partial file;
- return an immutable `IngestedImage` dataclass containing hash and three paths.

Do not accept a caller-supplied filesystem path through the API; this service consumes uploaded bytes.

The public implementation surface is:

```python
@dataclass(frozen=True)
class IngestedImage:
    sha256: str
    original_path: Path
    inference_path: Path
    thumbnail_path: Path


class ImageIngestor:
    MAX_BYTES = 20 * 1024 * 1024
    ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}

    def __init__(self, image_root: Path) -> None:
        self.image_root = image_root.resolve()

    def ingest(self, task_id: str, filename: str, content: bytes) -> IngestedImage:
        if not content or len(content) > self.MAX_BYTES:
            raise UnsupportedImage("image size must be between 1 byte and 20 MiB")
        digest = hashlib.sha256(content).hexdigest()
        task_root = (self.image_root / task_id).resolve()
        if self.image_root not in task_root.parents:
            raise UnsupportedImage("invalid task image directory")
        self._reserve_hash(task_root, digest)
        safe_name = Path(filename).name or "image"
        unique_name = f"{uuid4()}-{safe_name}"
        try:
            with Image.open(BytesIO(content)) as source:
                if source.format not in self.ALLOWED_FORMATS:
                    raise UnsupportedImage(f"unsupported image format: {source.format}")
                normalized = self._to_rgb(ImageOps.exif_transpose(source))
                original = self._atomic_bytes(task_root / "original" / unique_name, content)
                inference = self._atomic_jpeg(
                    task_root / "inference" / f"{unique_name}.jpg", normalized, 2048, 90
                )
                thumbnail = self._atomic_jpeg(
                    task_root / "thumbs" / f"{unique_name}.jpg", normalized, 1600, 82
                )
        except (UnidentifiedImageError, OSError) as exc:
            self._release_hash(task_root, digest)
            raise UnsupportedImage("image cannot be decoded") from exc
        return IngestedImage(digest, original, inference, thumbnail)
```

`_reserve_hash` and `_release_hash` update `hashes.json` through a temporary file and `Path.replace`; `_atomic_bytes` and `_atomic_jpeg` use the same replace pattern. `_to_rgb` composites RGBA/LA images over white before conversion.

- [ ] **Step 4: Run image tests**

Run: `backend/.venv/bin/pytest backend/tests/services/test_images.py -v`

Expected: three passing tests.

- [ ] **Step 5: Commit image ingestion**

```bash
git add backend/app/services backend/tests/services
git commit -m "feat: add safe image ingestion pipeline"
```

## Task 7: Add starter templates, prompt assembly, and template APIs

**Files:**
- Create: `backend/app/services/prompts.py`
- Create: `backend/app/api/templates.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/services/test_prompts.py`
- Create: `backend/tests/api/test_templates.py`

- [ ] **Step 1: Write failing tests for evidence policy and versioning**

```python
# backend/tests/services/test_prompts.py
from app.services.prompts import SYSTEM_EVIDENCE_POLICY, build_prompt, starter_templates


def test_all_starter_templates_enforce_visible_evidence() -> None:
    assert {item["slug"] for item in starter_templates()} == {"general", "manager", "chef"}
    prompt = build_prompt(starter_templates()[0], "IMG_001.png")
    assert SYSTEM_EVIDENCE_POLICY in prompt.system
    assert "not_visible" in prompt.system
    assert "气味" not in prompt.user


def test_prompt_contains_every_rule_id() -> None:
    template = starter_templates()[2]
    prompt = build_prompt(template, "chef.png")
    for rule in template["rules"]:
        assert rule["rule_id"] in prompt.user
```

API tests must assert that creating a template creates version 1, updating it creates version 2, retrieving version 1 remains unchanged, and a task-facing read response never exposes a mutable ORM object.

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_prompts.py backend/tests/api/test_templates.py -v
```

Expected: FAIL because prompt service and template router are absent.

- [ ] **Step 3: Implement the non-overridable evidence policy and three starter templates**

Use a frozen `PromptBundle(system: str, user: str)` dataclass. `SYSTEM_EVIDENCE_POLICY` must state all five approved rules: visible facts only, no demographic/role inference, `not_visible` for obscured items, visual location for every failure, and absence-is-not-violation except for required visible objects.

Starter rules must use these stable IDs:

```python
GENERAL_RULES = [
    {"rule_id": "hair", "name": "头发与发型", "text": "检查画面中可见的头发是否整齐、是否有明显异常染色或遮挡视线。"},
    {"rule_id": "face", "name": "面部仪容", "text": "检查画面中可见的胡须、妆容和面部整洁情况。"},
    {"rule_id": "accessories", "name": "饰品", "text": "检查画面中清晰可见的耳饰、项链、手链等是否符合规则。"},
    {"rule_id": "hands", "name": "手部与指甲", "text": "仅在手部清晰入镜时检查长指甲、明显污垢和有色美甲。"},
    {"rule_id": "uniform", "name": "制服整洁", "text": "检查制服是否配套，并识别明显皱褶、破损、污渍、挽袖或挽裤腿。"},
    {"rule_id": "badge", "name": "工牌佩戴", "text": "左胸区域清晰可见时，检查是否佩戴工牌。"},
    {"rule_id": "footwear", "name": "鞋袜", "text": "仅在鞋袜清晰入镜时检查明显破损、污渍或不规范穿着。"},
]

CHEF_RULES = [
    {"rule_id": "chef_uniform", "name": "厨师工服", "text": "检查可见工服是否完整、整洁并无明显油污或破损。"},
    {"rule_id": "apron", "name": "围裙", "text": "围裙入镜时检查是否整洁。"},
    {"rule_id": "hat_hair", "name": "厨师帽与头发", "text": "头部清晰入镜时检查厨师帽是否规范并遮盖头发。"},
    {"rule_id": "mask", "name": "口罩", "text": "仅当画面明确显示正在食品处理区操作时，检查口罩是否遮住口鼻。"},
    {"rule_id": "chef_hands", "name": "手部卫生", "text": "手部清晰入镜时检查有色美甲、明显伤口裸露或不规范包扎。"},
    {"rule_id": "unsafe_behavior", "name": "现场不规范行为", "text": "只记录当前画面直接捕获的吸烟、进食或对食物咳嗽等行为。"},
]
```

Manager uses `GENERAL_RULES` with role wording changed to 店总，不复制另一份规则常量。

- [ ] **Step 4: Implement template CRUD/version routes and seed-on-empty startup**

Add request models with rule IDs constrained to `^[a-z][a-z0-9_]{1,63}$`. On startup, seed the three templates only when the template table is empty. `PUT /api/templates/{id}` always creates a version; it never mutates an existing version row. Return `409 template_name_exists` for duplicate names.

```python
@router.put("/{template_id}", response_model=TemplateDetail)
def update_template(
    template_id: str,
    body: TemplateUpdate,
    session: Session = Depends(get_session),
) -> TemplateDetail:
    repo = TemplateRepository(session)
    template = repo.get(template_id)
    template.name = body.name
    template.description = body.description
    version = repo.add_version(
        template_id,
        [rule.model_dump() for rule in body.rules],
        body.model_params.model_dump(),
        system_prompt=SYSTEM_EVIDENCE_POLICY,
        task_prompt=body.task_prompt,
    )
    session.commit()
    return TemplateDetail.from_records(template, version)


def seed_templates_if_empty(session: Session) -> None:
    repo = TemplateRepository(session)
    if repo.count() != 0:
        return
    for seed in starter_templates():
        template = repo.create(seed["name"], seed["description"])
        repo.add_version(
            template.id,
            seed["rules"],
            seed["model_params"],
            SYSTEM_EVIDENCE_POLICY,
            seed["task_prompt"],
        )
    session.commit()
```

- [ ] **Step 5: Run prompt and API tests**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_prompts.py backend/tests/api/test_templates.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit templates and prompts**

```bash
git add backend/app backend/tests
git commit -m "feat: add versioned inspection prompt templates"
```

## Task 8: Implement Ark Responses and Chat adapters

**Files:**
- Create: `backend/app/services/ark.py`
- Create: `backend/app/services/retry.py`
- Create: `backend/app/assets/ark_test.png`
- Create: `backend/tests/services/test_ark.py`
- Create: `backend/tests/services/test_retry.py`
- Modify: `backend/app/api/settings.py`

- [ ] **Step 1: Write failing adapter tests with fake Ark clients**

```python
# backend/tests/services/test_ark.py
import json
from types import SimpleNamespace

from app.domain.results import Verdict
from app.services.ark import ArkInspectionService, ArkRuntimeConfig


VALID = {
    "schema_version": 1,
    "image_valid": True,
    "unverifiable_reason": None,
    "verdict": "pass",
    "summary": "制服整洁",
    "overall_confidence": 0.9,
    "findings": [
        {
            "rule_id": "uniform",
            "rule_name": "制服整洁",
            "status": "pass",
            "observation": "未见污渍",
            "visual_evidence": "上衣正面清晰可见",
            "confidence": 0.9,
        }
    ],
}


class FakeResponses:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.outputs.pop(0), usage=None)


def test_responses_adapter_sends_one_image_and_parses_schema(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses([json.dumps(VALID, ensure_ascii=False)])
    client = SimpleNamespace(responses=responses)
    service = ArkInspectionService(client=client)
    result = service.inspect_responses(
        ArkRuntimeConfig(
            base_url="https://ark.example/v3",
            model="seed",
            protocol="responses",
            thinking="disabled",
            timeout_seconds=120,
            max_retries=2,
        ),
        "system",
        "user",
        image,
    )
    assert result.verdict is Verdict.PASS
    content = responses.calls[0]["input"][0]["content"]
    assert sum(item["type"] == "input_image" for item in content) == 1
```

Add parallel coverage for Chat Completions, an invalid first response followed by a valid repair response, and a model result whose top-level verdict is corrected from `pass` to `fail` by `normalized()`.

- [ ] **Step 2: Write retry-policy tests**

```python
# backend/tests/services/test_retry.py
from app.services.retry import RetryDecision, RetryPolicy


def test_429_uses_retry_after() -> None:
    decision = RetryPolicy(max_retries=2).classify(429, {"retry-after": "3"}, 0)
    assert decision == RetryDecision(retry=True, delay_seconds=3.0, pause_task=False)


def test_auth_error_pauses_without_retry() -> None:
    decision = RetryPolicy(max_retries=2).classify(401, {}, 0)
    assert decision.retry is False
    assert decision.pause_task is True
```

- [ ] **Step 3: Run tests and confirm missing services**

Run: `backend/.venv/bin/pytest backend/tests/services/test_ark.py backend/tests/services/test_retry.py -v`

Expected: FAIL because Ark and retry services do not exist.

- [ ] **Step 4: Implement the Ark service**

`ArkInspectionService` must accept an injected client for tests and otherwise create `volcenginesdkarkruntime.Ark(base_url=config.base_url, api_key=api_key)`. Use exactly one image content item and one text content item per inspection.

```python
@dataclass(frozen=True)
class ArkRuntimeConfig:
    base_url: str
    model: str
    protocol: Literal["responses", "chat"]
    thinking: Literal["disabled", "auto", "enabled"]
    timeout_seconds: int
    max_retries: int


def inspect(
    self,
    config: ArkRuntimeConfig,
    system_prompt: str,
    user_prompt: str,
    image_path: Path,
) -> InspectionResult:
    if config.protocol == "responses":
        return self.inspect_responses(config, system_prompt, user_prompt, image_path)
    return self.inspect_chat(config, system_prompt, user_prompt, image_path)
```

Responses request shape:

```python
response = client.responses.create(
    model=config.model,
    input=[
        {
            "role": "user",
            "content": [
                {"type": "input_image", "image_url": image_data_url},
                {"type": "input_text", "text": user_prompt},
            ],
        }
    ],
    instructions=system_prompt,
    text={
        "format": {
            "type": "json_schema",
            "name": "inspection_result",
            "strict": True,
            "schema": InspectionResult.model_json_schema(),
        }
    },
    thinking={"type": config.thinking},
)
```

Chat request shape:

```python
response = client.chat.completions.create(
    model=config.model,
    messages=[
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        },
    ],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "inspection_result",
            "strict": True,
            "schema": InspectionResult.model_json_schema(),
        },
    },
)
```

If the model rejects native JSON Schema with an explicit unsupported-parameter response, retry that image once with `json_object` plus the serialized schema in the prompt. If parsing still fails, make one repair call containing only the invalid text and the schema; never add a second image to a request. Strip Markdown code fences before `json.loads`, validate with Pydantic, then call `normalized()`.

- [ ] **Step 5: Implement retry classification**

`RetryPolicy.classify` must use `Retry-After` for 429, pause on 401/403 and non-rate-limit 4xx parameter errors, retry timeouts and 5xx up to `max_retries`, and otherwise return a terminal decision. Exponential delay is `min(30, 2 ** attempt) + random.uniform(0, 0.5)`.

```python
@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay_seconds: float = 0
    pause_task: bool = False


class RetryPolicy:
    def __init__(self, max_retries: int) -> None:
        self.max_retries = max_retries

    def classify(self, status: int | None, headers: Mapping[str, str], attempt: int) -> RetryDecision:
        if status in {401, 403}:
            return RetryDecision(False, pause_task=True)
        if status == 429 and attempt < self.max_retries:
            delay = float(headers.get("retry-after", "0"))
            return RetryDecision(True, delay or self._delay(attempt))
        if (status is None or status >= 500) and attempt < self.max_retries:
            return RetryDecision(True, self._delay(attempt))
        if status is not None and 400 <= status < 500:
            return RetryDecision(False, pause_task=True)
        return RetryDecision(False)

    @staticmethod
    def _delay(attempt: int) -> float:
        return min(30.0, float(2**attempt)) + random.uniform(0, 0.5)
```

- [ ] **Step 6: Add a real vision capability test endpoint**

`POST /api/settings/ark/test` loads a bundled 32×32 PNG, asks for the fixed schema, and returns `{ok, protocol, model, latency_ms}`. It uses the same adapter and secret store as production. Never include the API Key or raw upstream headers in the response.

Create the deterministic test asset once:

```bash
mkdir -p backend/app/assets
backend/.venv/bin/python -c 'from PIL import Image; Image.new("RGB", (32, 32), "white").save("backend/app/assets/ark_test.png")'
```

```python
@router.post("/ark/test", response_model=ArkTestResult)
def test_ark_connection(deps: ArkDependencies = Depends(get_ark_dependencies)) -> ArkTestResult:
    started = time.monotonic()
    deps.inspector.inspect(
        deps.runtime_config,
        SYSTEM_EVIDENCE_POLICY,
        "检查图片是否可读取，并按固定结构返回。",
        deps.bundled_test_image,
    )
    return ArkTestResult(
        ok=True,
        protocol=deps.runtime_config.protocol,
        model=deps.runtime_config.model,
        latency_ms=round((time.monotonic() - started) * 1000),
    )
```

- [ ] **Step 7: Run adapter tests**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_ark.py backend/tests/services/test_retry.py backend/tests/api/test_settings.py -v
backend/.venv/bin/ruff check backend/app backend/tests
```

Expected: all tests pass and lint is clean.

- [ ] **Step 8: Commit Ark integration**

```bash
git add backend/app backend/tests
git commit -m "feat: integrate Ark multimodal inspection"
```

## Task 9: Build the persistent task runner and SSE event broker

**Files:**
- Create: `backend/app/services/events.py`
- Create: `backend/app/services/runner.py`
- Create: `backend/tests/services/test_runner.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing runner tests for concurrency, retry, pause, and restart**

Create a `FakeInspector` that tracks active calls and can return a result or raise `ArkRequestError(status_code, headers, code)`. The tests must prove:

```python
async def test_runner_never_exceeds_configured_concurrency(task_fixture) -> None:
    inspector = FakeInspector(delay=0.02)
    runner = TaskRunner(task_fixture.sessions, inspector, concurrency=3)
    await runner.run_task(task_fixture.task_id)
    assert inspector.max_active == 3
    assert task_fixture.count_images("succeeded") == 8


async def test_runner_pauses_task_on_auth_error(task_fixture) -> None:
    inspector = FakeInspector(error_status=401)
    runner = TaskRunner(task_fixture.sessions, inspector, concurrency=3)
    await runner.run_task(task_fixture.task_id)
    assert task_fixture.task_status() == "paused"
```

Also assert 429 retries twice, a terminal malformed result becomes `failed`, and `recover_interrupted_work` leaves the task paused.

- [ ] **Step 2: Run tests and confirm runner is missing**

Run: `backend/.venv/bin/pytest backend/tests/services/test_runner.py -v`

Expected: FAIL because event and runner services are absent.

- [ ] **Step 3: Implement the event broker**

`TaskEventBroker` maintains a bounded `asyncio.Queue[dict]` per subscriber. `publish(task_id, event)` copies to all current subscribers. `subscribe(task_id)` is an async context manager that registers a queue and always unregisters it. Drop the oldest event if a queue is full; clients recover authoritative state through REST.

```python
class TaskEventBroker:
    def __init__(self, queue_size: int = 100) -> None:
        self.queue_size = queue_size
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, object]]]] = defaultdict(set)

    async def publish(self, task_id: str, event: dict[str, object]) -> None:
        for queue in tuple(self._subscribers[task_id]):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event.copy())

    @asynccontextmanager
    async def subscribe(self, task_id: str):
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(self.queue_size)
        self._subscribers[task_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[task_id].discard(queue)
```

- [ ] **Step 4: Implement the database-backed runner**

`TaskRunner` must:

1. create `concurrency` worker coroutines guarded by a semaphore;
2. open a new SQLAlchemy session for every claim and every result write;
3. stop claiming when task state is not `running`;
4. call the synchronous Ark SDK through `asyncio.to_thread`;
5. apply `RetryPolicy` and record each attempt;
6. publish `image.started`, `image.succeeded`, `image.failed`, and `task.updated` events;
7. move a task with no queued/running images to `exporting` when every image succeeded, otherwise `review_required`;
8. never start report export inside an open model-result transaction.

Expose `start(task_id)`, `pause(task_id)`, `resume(task_id)`, `cancel(task_id)`, and `retry_image(image_id)`. Keep one in-memory `asyncio.Task` per running task and reject duplicate starts.

```python
async def _worker(self, task_id: str) -> None:
    while self._task_is_running(task_id):
        image = self._claim_next_image(task_id)
        if image is None:
            return
        await self.events.publish(task_id, {"type": "image.started", "image_id": image.id})
        try:
            result = await asyncio.to_thread(self._inspect_with_retry, task_id, image)
            self._save_success(image.id, result)
            await self.events.publish(
                task_id, {"type": "image.succeeded", "image_id": image.id}
            )
        except PauseTaskError as exc:
            self._save_failure_and_pause(task_id, image.id, exc)
            return
        except InspectionFailed as exc:
            self._save_failure(image.id, exc)
            await self.events.publish(
                task_id, {"type": "image.failed", "image_id": image.id}
            )
```

- [ ] **Step 5: Wire startup recovery without auto-resuming external calls**

Use FastAPI lifespan to call `recover_interrupted_work()` once, start broker infrastructure, and close outstanding runner tasks on shutdown. Do not automatically resume paused tasks.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    with app.state.sessions() as session:
        TaskRepository(session).recover_interrupted_work()
        session.commit()
    yield
    await app.state.runner.shutdown()
```

- [ ] **Step 6: Run runner tests**

Run: `backend/.venv/bin/pytest backend/tests/services/test_runner.py -v`

Expected: all runner tests pass; the concurrency assertion is stable across five repeated runs.

- [ ] **Step 7: Commit the runner**

```bash
git add backend/app/services backend/app/main.py backend/tests/services
git commit -m "feat: add restart-safe local task runner"
```

## Task 10: Expose task, upload, lifecycle, retry, and SSE APIs

**Files:**
- Create: `backend/app/api/tasks.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/api/test_tasks.py`

- [ ] **Step 1: Write a failing end-to-end API test using fake Ark and report services**

```python
# backend/tests/api/test_tasks.py
def test_create_upload_start_and_fetch_results(app_harness, png_file) -> None:
    client = app_harness.client
    template_id = app_harness.seeded_template_id
    created = client.post("/api/tasks", json={"name": "门店巡检", "template_id": template_id})
    assert created.status_code == 201
    task_id = created.json()["id"]
    uploaded = client.post(
        f"/api/tasks/{task_id}/images",
        files=[("files", ("one.png", png_file, "image/png"))],
    )
    assert uploaded.status_code == 201
    assert len(uploaded.json()["accepted"]) == 1
    assert client.post(f"/api/tasks/{task_id}/start").status_code == 202
    app_harness.runner.wait_until_idle(task_id)
    detail = client.get(f"/api/tasks/{task_id}").json()
    assert detail["counts"]["succeeded"] == 1
    assert detail["images"][0]["result"]["verdict"] == "pass"
```

Add tests for 101 files rejected before ingestion, duplicate hashes reported in `skipped`, pause/resume, cancel, retry only failed images, and `Last-Event-ID` reconnect behavior.

- [ ] **Step 2: Run API tests and confirm routes are missing**

Run: `backend/.venv/bin/pytest backend/tests/api/test_tasks.py -v`

Expected: FAIL with task endpoint 404.

- [ ] **Step 3: Implement task and upload routes**

Implement these status codes and contracts exactly:

- `POST /api/tasks` → 201 with immutable template/model snapshots.
- `GET /api/tasks` → 200 newest first with status counts.
- `GET /api/tasks/{id}` → 200 with ordered images and current results.
- `POST /api/tasks/{id}/images` → 201 `{accepted, skipped, rejected}`; only valid for draft tasks.
- `GET /api/tasks/{id}/images/{image_id}/content?variant=original|thumbnail` → streamed local file after membership validation.
- `POST /api/tasks/{id}/start` → 202.
- `POST /api/tasks/{id}/pause|resume|cancel` → 202.
- `POST /api/tasks/{id}/images/{image_id}/retry` → 202 only for failed images.
- `GET /api/tasks/{id}/events` → `text/event-stream` with heartbeat every 15 seconds.

Map `LookupError` to 404, illegal state changes to 409, upload validation to 422, and unavailable Ark/Feishu configuration to 409 with stable error codes.

```python
@router.post("/{task_id}/images", status_code=201, response_model=UploadSummary)
async def upload_images(
    task_id: str,
    files: Annotated[list[UploadFile], File()],
    deps: TaskDependencies = Depends(get_task_dependencies),
) -> UploadSummary:
    task = deps.repo.get_task(task_id)
    if task.status != TaskStatus.DRAFT.value:
        raise ApiError(409, "task_not_draft", "只能向草稿任务添加图片")
    if deps.repo.count_images(task_id) + len(files) > 100:
        raise ApiError(422, "task_image_limit", "单个任务最多 100 张图片")
    return await deps.upload_service.ingest_many(task_id, files)


@router.get("/{task_id}/events")
async def task_events(task_id: str, broker: TaskEventBroker = Depends(get_event_broker)):
    async def stream():
        async with broker.subscribe(task_id) as queue:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield ServerSentEvent(data=json.dumps(event), event=str(event["type"]))
                except TimeoutError:
                    yield ServerSentEvent(comment="heartbeat")
    return EventSourceResponse(stream())
```

- [ ] **Step 4: Add API-level image count and ownership checks**

Count accepted existing images plus incoming files before writing any bytes. Reject more than 100 with `task_image_limit`. Verify every image path comes from the database and lies under the configured data directory before streaming.

```python
def require_owned_path(candidate: str, data_dir: Path) -> Path:
    resolved = Path(candidate).resolve(strict=True)
    root = data_dir.resolve(strict=True)
    if root != resolved and root not in resolved.parents:
        raise ApiError(403, "image_path_outside_data_dir", "图片路径不属于应用数据目录")
    return resolved
```

- [ ] **Step 5: Run API and service tests**

Run: `backend/.venv/bin/pytest backend/tests/api/test_tasks.py backend/tests/services/test_runner.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit task APIs**

```bash
git add backend/app/api backend/app/schemas.py backend/app/main.py backend/tests/api
git commit -m "feat: expose batch inspection task APIs"
```

## Task 11: Implement Feishu OAuth v3 with PKCE and token refresh

**Files:**
- Create: `backend/app/services/feishu_oauth.py`
- Create: `backend/app/api/feishu_auth.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/services/test_feishu_oauth.py`
- Create: `backend/tests/api/test_feishu_auth.py`

- [ ] **Step 1: Write failing tests for signed state, token storage, refresh, and disconnect**

Tests must assert:

```python
def test_oauth_state_rejects_tampering(oauth_service) -> None:
    state = oauth_service.make_state(now=1_787_398_400)
    oauth_service.verify_state(state, now=1_787_398_500)
    with pytest.raises(InvalidOAuthState):
        oauth_service.verify_state(state, now=1_787_398_500)
    with pytest.raises(InvalidOAuthState):
        oauth_service.verify_state(state + "x", now=1_787_398_500)


async def test_exchange_saves_tokens_only_in_secret_store(oauth_service, fake_http) -> None:
    await oauth_service.exchange_code("code-1")
    assert oauth_service.secrets.get("feishu_access_token") == "access-1"
    assert oauth_service.secrets.get("feishu_refresh_token") == "refresh-1"
    assert "access-1" not in oauth_service.database_bytes()
```

API tests cover `/start`, `/callback`, `/status`, and `/disconnect` and verify callback errors redirect to `/settings?feishu=error` without leaking the authorization code.

- [ ] **Step 2: Run tests and confirm OAuth service is absent**

Run: `backend/.venv/bin/pytest backend/tests/services/test_feishu_oauth.py backend/tests/api/test_feishu_auth.py -v`

Expected: FAIL because OAuth modules do not exist.

- [ ] **Step 3: Implement signed, expiring, single-use OAuth state**

Create state as URL-safe Base64 of `timestamp.random_nonce.signature`; compute the signature with HMAC-SHA256 using the Feishu App Secret from keyring. Save only the SHA-256 digest and expiry in the `app_settings` row `feishu_pending_oauth_state`. Generate a PKCE verifier, save it in keyring under `feishu_oauth_code_verifier`, and put its S256 challenge in the authorization URL. Verification must atomically consume the state row, use `hmac.compare_digest`, and require a maximum age of 10 minutes. This makes callbacks single-use even across application restarts.

Use the explicit user scopes `offline_access`, `docx:document`, and `drive:drive`; document why `offline_access` is required for refresh tokens and why the document/drive permissions are required for Docx blocks, target folders, and media uploads.

```python
def make_state(self, now: int | None = None) -> str:
    timestamp = now or int(time.time())
    nonce = secrets.token_urlsafe(24)
    payload = f"{timestamp}.{nonce}"
    signature = hmac.new(
        self._app_secret().encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    encoded = base64.urlsafe_b64encode(f"{payload}.{signature}".encode()).decode()
    self.state_store.save(hashlib.sha256(encoded.encode()).hexdigest(), timestamp + 600)
    return encoded


def verify_state(self, encoded: str, now: int | None = None) -> None:
    timestamp_text, nonce, signature = base64.urlsafe_b64decode(encoded).decode().split(".")
    payload = f"{timestamp_text}.{nonce}"
    expected = hmac.new(
        self._app_secret().encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    current = now or int(time.time())
    state_digest = hashlib.sha256(encoded.encode()).hexdigest()
    if not self.state_store.consume(state_digest, current):
        raise InvalidOAuthState("OAuth state was not issued or was already used")
    if not hmac.compare_digest(signature, expected) or current - int(timestamp_text) > 600:
        raise InvalidOAuthState("OAuth state is invalid or expired")


def authorization_url(self) -> str:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    self.secrets.set("feishu_oauth_code_verifier", verifier)
    params = {
        "client_id": self.app_id,
        "redirect_uri": self.redirect_uri,
        "response_type": "code",
        "scope": " ".join(self.scopes),
        "state": self.make_state(),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{self.AUTHORIZATION_URL}?{urlencode(params)}"
```

- [ ] **Step 4: Implement OAuth v3 exchange, PKCE, and refresh**

Use:

- authorization URL `https://accounts.feishu.cn/open-apis/authen/v1/authorize`;
- token URL `https://accounts.feishu.cn/oauth/v3/token`;
- grant types `authorization_code` and `refresh_token`;
- configured callback `http://127.0.0.1:8000/api/auth/feishu/callback`.

The authorization URL includes `code_challenge` and `code_challenge_method=S256`; token exchange includes the matching `code_verifier`. Persist access token, refresh token, and their expirations in keyring. Delete the one-time verifier after exchange whether the request succeeds or fails. `get_valid_access_token()` refreshes when fewer than 120 seconds remain. A failed refresh deletes stale tokens and raises `FeishuReauthorizationRequired`.

```python
async def exchange_code(self, code: str) -> None:
    verifier = self.secrets.get("feishu_oauth_code_verifier")
    if verifier is None:
        raise InvalidOAuthState("PKCE verifier is missing")
    try:
        response = await self.http.post(
            self.TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": self.app_id,
                "client_secret": self._app_secret(),
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": verifier,
            },
        )
        response.raise_for_status()
        token = OAuthTokenResponse.model_validate(response.json())
        self._save_token_response(token)
    finally:
        self.secrets.delete("feishu_oauth_code_verifier")
```

- [ ] **Step 5: Implement auth routes and status response**

`/start` returns the authorization URL, `/callback` exchanges then redirects to `/settings?feishu=connected`, `/status` returns only `{configured, connected, expires_at}`, and `/disconnect` deletes both tokens without deleting App ID or App Secret.

```python
@router.get("/callback")
async def callback(
    code: str,
    state: str,
    oauth: FeishuOAuthService = Depends(get_feishu_oauth),
) -> RedirectResponse:
    try:
        oauth.verify_state(state)
        await oauth.exchange_code(code)
    except (InvalidOAuthState, httpx.HTTPError):
        return RedirectResponse("/settings?feishu=error", status_code=303)
    return RedirectResponse("/settings?feishu=connected", status_code=303)
```

- [ ] **Step 6: Run OAuth tests**

Run: `backend/.venv/bin/pytest backend/tests/services/test_feishu_oauth.py backend/tests/api/test_feishu_auth.py -v`

Expected: all tests pass and token strings do not appear in captured logs.

- [ ] **Step 7: Commit OAuth support**

```bash
git add backend/app backend/tests
git commit -m "feat: add Feishu user OAuth"
```

## Task 12: Build resumable Feishu Docx reports

**Files:**
- Create: `backend/app/services/feishu_client.py`
- Create: `backend/app/services/reports.py`
- Modify: `backend/app/api/tasks.py`
- Create: `backend/tests/services/test_reports.py`
- Create: `backend/tests/api/test_reports.py`

- [ ] **Step 1: Write failing report projection and resume tests**

```python
# backend/tests/services/test_reports.py
def test_report_projection_matches_result_counts(report_fixture) -> None:
    projection = report_fixture.builder.project(report_fixture.task_id)
    assert projection.counts == {
        "pass": 1,
        "fail": 1,
        "unverifiable": 1,
        "failed": 0,
    }
    assert [item.filename for item in projection.items] == ["001.png", "002.png", "003.png"]


async def test_retry_resumes_existing_document_without_duplicate_blocks(report_fixture) -> None:
    report_fixture.feishu.fail_after_operations = 3
    first = await report_fixture.builder.export(report_fixture.task_id)
    assert first.status == "failed"
    document_id = first.document_id
    report_fixture.feishu.fail_after_operations = None
    second = await report_fixture.builder.export(report_fixture.task_id)
    assert second.document_id == document_id
    assert second.status == "succeeded"
    assert report_fixture.feishu.created_documents == 1
    assert report_fixture.feishu.duplicate_operation_keys == []
```

Add tests for image block creation before media upload, serial media uploads, token refresh, 429 retry, user-requested new report version, and report generation never invoking the Ark adapter.

Add a restart test that changes an interrupted `ReportExport(status="running")` to `failed` with `error_code="report_interrupted"`, moves the parent task to `review_required`, and confirms a user retry resumes the same document and cursor.

- [ ] **Step 2: Run tests and confirm report services are missing**

Run: `backend/.venv/bin/pytest backend/tests/services/test_reports.py backend/tests/api/test_reports.py -v`

Expected: FAIL because report modules and endpoint do not exist.

- [ ] **Step 3: Implement a rate-limited Feishu client**

`FeishuClient` wraps `httpx.AsyncClient` and `FeishuOAuthService`. Provide methods:

- `create_document(title, folder_token, client_token) -> DocumentRef`;
- `create_blocks(document_id, parent_block_id, blocks, operation_key) -> list[BlockRef]`;
- `create_image_block(document_id, parent_block_id, operation_key) -> BlockRef`;
- `upload_docx_image(block_id, filename, content) -> str`.

Limit document mutations to 3 requests/second and serialize media uploads with an `asyncio.Lock`, staying below 5 QPS. Retry 429 and 5xx with the common retry policy. Use the `ReportExport.id` as the document-creation `client_token`, and derive a deterministic `client_token` from each operation key for block creation. Persist completed operation keys locally before advancing the export cursor. This closes both ordinary retry and ambiguous-network-response duplication paths.

```python
async def upload_docx_image(self, block_id: str, filename: str, content: bytes) -> str:
    async with self._media_lock:
        await self._media_rate_limit.wait()
        response = await self._request(
            "POST",
            "/open-apis/drive/v1/medias/upload_all",
            data={
                "file_name": filename,
                "parent_type": "docx_image",
                "parent_node": block_id,
                "size": str(len(content)),
            },
            files={"file": (filename, content, "image/jpeg")},
        )
        return str(response["data"]["file_token"])
```

- [ ] **Step 4: Implement report projection and block generation**

Create immutable projection dataclasses for metadata, counts, rule summary, and ordered image items. Generate blocks in the approved order: AI notice, task metadata, four count cards represented as a table, issue summary table, per-image headings/details, failure list, trace metadata.

For each image item, create and persist the image block ID first, then upload the thumbnail with `parent_type=docx_image` and `parent_node=block_id`. Keep text block batches within current Feishu API limits and persist the export cursor after every successful batch or image.

```python
async def _write_image_item(self, export: ReportExport, item: ReportItem) -> None:
    operation_key = f"image:{item.image_id}"
    if self.repo.operation_completed(export.id, operation_key):
        return
    block = await self.feishu.create_image_block(
        export.document_id,
        export.document_id,
        operation_key,
    )
    self.repo.save_remote_block(export.id, operation_key, block.id)
    content = Path(item.thumbnail_path).read_bytes()
    await self.feishu.upload_docx_image(block.id, item.filename, content)
    self.repo.mark_operation_completed(export.id, operation_key)
```

- [ ] **Step 5: Implement automatic and manual report triggers**

When the runner transitions an all-success task to `exporting`, enqueue `ReportBuilder.export(task_id)`. For `review_required`, `POST /api/tasks/{id}/report` requires `{"include_failures": true}`. `POST /api/tasks/{id}/report/new-version` always creates a new `ReportExport`; ordinary retries reuse the active failed export.

At application startup, `recover_interrupted_exports()` marks running exports as retryable failures and moves their tasks to `review_required`; it does not make external calls until the user presses retry.

```python
@router.post("/{task_id}/report", status_code=202)
async def generate_report(
    task_id: str,
    body: ReportRequest,
    reports: ReportCoordinator = Depends(get_report_coordinator),
) -> dict[str, str]:
    export_id = await reports.enqueue(task_id, include_failures=body.include_failures)
    return {"export_id": export_id, "status": "accepted"}
```

- [ ] **Step 6: Run report tests**

Run: `backend/.venv/bin/pytest backend/tests/services/test_reports.py backend/tests/api/test_reports.py -v`

Expected: all report tests pass, including injected interruption and resume.

- [ ] **Step 7: Commit reporting**

```bash
git add backend/app backend/tests
git commit -m "feat: export resumable Feishu inspection reports"
```

## Task 13: Scaffold the React application shell and API client

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.app.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/app/router.tsx`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/styles.css`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/app/App.test.tsx`

- [ ] **Step 1: Create the frontend package and failing shell test**

```json
{
  "name": "vlm-inspector-ui",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "test:watch": "vitest",
    "e2e": "playwright test"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.66.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.1.0",
    "zod": "^3.24.0"
  },
  "devDependencies": {
    "@playwright/test": "^1.50.0",
    "@testing-library/jest-dom": "^6.6.0",
    "@testing-library/react": "^16.1.0",
    "@testing-library/user-event": "^14.6.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.0",
    "jsdom": "^26.0.0",
    "typescript": "~5.7.0",
    "vite": "^6.1.0",
    "vitest": "^3.0.0"
  }
}
```

```tsx
// frontend/src/app/App.test.tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";

test("renders the four primary navigation destinations", () => {
  render(<MemoryRouter><App /></MemoryRouter>);
  expect(screen.getByRole("link", { name: "运行任务" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "提示词模板" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "历史记录" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "设置" })).toBeInTheDocument();
});
```

- [ ] **Step 2: Install and verify the test fails**

Run:

```bash
npm --prefix frontend install
npm --prefix frontend test -- App.test.tsx
```

Expected: FAIL because `App` does not exist.

- [ ] **Step 3: Implement the shell, routes, typed client, and approved visual tokens**

```ts
// frontend/vite.config.ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
```

```ts
// frontend/src/test/setup.ts
import "@testing-library/jest-dom/vitest";
```

Implement a fixed dark sidebar and warm-neutral content surface. Use these CSS custom properties:

```css
:root {
  color: #252522;
  background: #f3efe9;
  font-family: Inter, "PingFang SC", "Microsoft YaHei", sans-serif;
  --surface: #fffdf9;
  --surface-muted: #ebe6de;
  --ink: #252522;
  --ink-muted: #716a62;
  --accent: #6f7f63;
  --danger: #a24635;
  --warning: #b27b36;
  --line: #d7d0c7;
  --radius: 12px;
}
```

`ApiClient` must parse error envelopes `{code, message, details}` and never log request bodies. Define TypeScript unions matching backend status enums exactly. Configure Vite proxy `/api` to `http://127.0.0.1:8000`.

- [ ] **Step 4: Run frontend tests and production build**

Run:

```bash
npm --prefix frontend test
npm --prefix frontend run build
```

Expected: shell test passes and `frontend/dist` is produced.

- [ ] **Step 5: Commit the frontend shell**

```bash
git add frontend
git commit -m "chore: scaffold React inspection console"
```

## Task 14: Build the settings and OAuth user interface

**Files:**
- Create: `frontend/src/features/settings/SettingsPage.tsx`
- Create: `frontend/src/features/settings/SettingsPage.test.tsx`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/api/types.ts`

- [ ] **Step 1: Write failing settings UI tests**

Tests must verify masked configured states, client validation for concurrency 1–5, `测试并保存` progress/success, Feishu connect opening the returned authorization URL, OAuth callback query banners, and no secret value rendered after save.

```tsx
test("replaces the submitted Ark key with configured state", async () => {
  server.use(successfulArkSettingsHandler);
  renderSettings();
  await user.type(screen.getByLabelText("API Key"), "ark-secret-value");
  await user.click(screen.getByRole("button", { name: "测试并保存" }));
  expect(await screen.findByText("连接测试通过")).toBeInTheDocument();
  expect(screen.queryByDisplayValue("ark-secret-value")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run the test and confirm the page is absent**

Run: `npm --prefix frontend test -- SettingsPage.test.tsx`

Expected: FAIL because `SettingsPage` does not exist.

- [ ] **Step 3: Implement Ark and Feishu cards**

Use controlled forms but clear secret inputs after successful save. Ark fields: Base URL, protocol, model/Endpoint, API Key, concurrency, timeout, retries, thinking. Feishu fields: App ID, App Secret, callback URL read-only, report folder, connection state, connect/reconnect/disconnect. Disable submit while requests are running and render backend error codes as actionable messages.

The page must never place secret values in URL parameters, browser storage, query cache, or component state after save completion.

```tsx
export function SettingsPage() {
  const [arkKey, setArkKey] = useState("");
  const ark = useQuery({ queryKey: ["settings", "ark"], queryFn: api.getArkSettings });
  const saveArk = useMutation({
    mutationFn: (values: ArkSettingsForm) => api.saveAndTestArk(
      Object.assign({}, values, { api_key: arkKey || null })
    ),
    onSuccess: () => {
      setArkKey("");
      queryClient.invalidateQueries({ queryKey: ["settings", "ark"] });
    },
  });
  return (
    <main className="settings-grid">
      <ArkSettingsCard
        settings={ark.data}
        secretValue={arkKey}
        onSecretChange={setArkKey}
        onSubmit={(values) => saveArk.mutate(values)}
        pending={saveArk.isPending}
      />
      <FeishuSettingsCard />
    </main>
  );
}
```

- [ ] **Step 4: Run page tests and build**

Run:

```bash
npm --prefix frontend test -- SettingsPage.test.tsx
npm --prefix frontend run build
```

Expected: all settings tests pass and TypeScript has no errors.

- [ ] **Step 5: Commit settings UI**

```bash
git add frontend/src
git commit -m "feat: add secure connection settings UI"
```

## Task 15: Build prompt template management and single-image testing

**Files:**
- Create: `frontend/src/features/templates/TemplatesPage.tsx`
- Create: `frontend/src/features/templates/TemplateEditor.tsx`
- Create: `frontend/src/features/templates/TemplatesPage.test.tsx`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/api/types.ts`
- Modify: `backend/app/api/templates.py`
- Create: `backend/tests/api/test_template_test_run.py`

- [ ] **Step 1: Write failing UI and API tests**

Backend test: upload one PNG to `/api/templates/{id}/test`, assert the fake Ark adapter is called once, fixed result schema is returned, and no `Task` or `TaskImage` row is created.

```python
def test_template_test_run_does_not_create_task(app_harness, png_file) -> None:
    template_id = app_harness.seeded_template_id
    response = app_harness.client.post(
        f"/api/templates/{template_id}/test",
        files={"file": ("sample.png", png_file, "image/png")},
    )
    assert response.status_code == 200
    assert response.json()["verdict"] == "pass"
    assert app_harness.count_rows("tasks") == 0
    assert app_harness.count_rows("task_images") == 0
    assert app_harness.inspector.call_count == 1
```

Frontend tests: select each starter template, add/remove/reorder a rule, reject duplicate or invalid rule IDs, preview final prompt, save a new version, and run a single-image test that renders the same result card used by task details.

```tsx
test("saving an edited template displays the new version", async () => {
  server.use(templateListHandler, templateUpdateHandler({ version: 2 }));
  renderTemplates();
  await user.click(await screen.findByRole("button", { name: "通用员工仪容仪表" }));
  await user.type(screen.getByLabelText("任务补充说明"), "重点检查工牌");
  await user.click(screen.getByRole("button", { name: "保存新版本" }));
  expect(await screen.findByText("当前版本 v2")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests and confirm missing behavior**

Run:

```bash
backend/.venv/bin/pytest backend/tests/api/test_template_test_run.py -v
npm --prefix frontend test -- TemplatesPage.test.tsx
```

Expected: both suites fail on missing endpoint/page behavior.

- [ ] **Step 3: Implement isolated template testing in the backend**

The endpoint must ingest into a temporary directory created with `TemporaryDirectory`, run the active template version and current Ark settings, return the normalized result, and delete all temporary files on success or failure. It must not call repositories that create tasks or reports.

```python
@router.post("/{template_id}/test", response_model=InspectionResult)
async def test_template(
    template_id: str,
    file: UploadFile,
    deps: TemplateTestDependencies = Depends(get_template_test_dependencies),
) -> InspectionResult:
    version = deps.templates.get_current_version(template_id)
    with TemporaryDirectory() as directory:
        image = ImageIngestor(Path(directory)).ingest("test", file.filename or "image", await file.read())
        bundle = build_prompt(version.to_prompt_source(), image.original_path.name)
        return await asyncio.to_thread(
            deps.inspector.inspect,
            deps.runtime_config,
            bundle.system,
            bundle.user,
            image.inference_path,
        )
```

- [ ] **Step 4: Implement the template list/editor/test workspace**

Use a two-column layout: template list left, editor right. Keep the system evidence policy read-only and visually distinct. Rule rows expose stable ID, name, and text. The preview modal displays the final system and user prompts. Saving returns and displays the new version number. The test drawer accepts exactly one image and shows large preview plus structured findings.

```tsx
return (
  <main className="template-workspace">
    <TemplateList templates={templates.data ?? []} selectedId={selectedId} onSelect={setSelectedId} />
    <TemplateEditor
      template={selected.data}
      systemPolicy={selected.data?.system_prompt ?? ""}
      onSave={(draft) => saveVersion.mutate(draft)}
      onPreview={setPreview}
      onTestImage={runTest}
    />
    <PromptPreview open={preview !== null} bundle={preview} onClose={() => setPreview(null)} />
    <TemplateTestDrawer result={testResult} onClose={() => setTestResult(null)} />
  </main>
);
```

- [ ] **Step 5: Run tests and build**

Run:

```bash
backend/.venv/bin/pytest backend/tests/api/test_template_test_run.py -v
npm --prefix frontend test -- TemplatesPage.test.tsx
npm --prefix frontend run build
```

Expected: all template tests pass and production build succeeds.

- [ ] **Step 6: Commit template UI and test endpoint**

```bash
git add backend frontend/src
git commit -m "feat: add prompt template workspace"
```

## Task 16: Build the batch task workspace

**Files:**
- Create: `frontend/src/features/tasks/TaskPage.tsx`
- Create: `frontend/src/features/tasks/TaskToolbar.tsx`
- Create: `frontend/src/features/tasks/ImageGallery.tsx`
- Create: `frontend/src/features/tasks/ImageDetail.tsx`
- Create: `frontend/src/features/tasks/useTaskEvents.ts`
- Create: `frontend/src/features/tasks/TaskPage.test.tsx`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/api/types.ts`

- [ ] **Step 1: Write failing workspace tests**

Tests must cover drag/drop and file-picker upload, accepted/skipped/rejected summaries, 100-file client guard, start/pause/resume/cancel buttons by state, SSE updates, REST refetch after reconnect, six status badges, filters, selecting a thumbnail to show the large image and evidence, failed-image retry, and “带失败项生成报告” confirmation.

```tsx
test("keeps gallery and single-image evidence visible together", async () => {
  server.use(taskDetailWithThreeImagesHandler);
  renderTask("task-1");
  expect(await screen.findAllByRole("img", { name: /巡检图片/ })).toHaveLength(3);
  await user.click(screen.getByRole("button", { name: "查看 IMG_002.png" }));
  expect(screen.getByRole("img", { name: "IMG_002.png 大图" })).toBeInTheDocument();
  expect(screen.getByText("左胸区域无遮挡，未出现工牌")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the test and confirm the workspace is absent**

Run: `npm --prefix frontend test -- TaskPage.test.tsx`

Expected: FAIL because task workspace components do not exist.

- [ ] **Step 3: Implement task creation, upload, and controls**

The create panel requests task name and template. Upload sends multipart files in ordered chunks of at most 10 files and merges all three response lists (`accepted`, `skipped`, `rejected`). Controls use backend task state, not optimistic local state. Pausing leaves running thumbnails active until their terminal events arrive.

```tsx
function chunk<T>(items: T[], size: number): T[][] {
  return Array.from({ length: Math.ceil(items.length / size) }, (_, index) =>
    items.slice(index * size, (index + 1) * size)
  );
}

function emptyUploadSummary(): UploadSummary {
  return { accepted: [], skipped: [], rejected: [] };
}

function mergeUploadSummary(target: UploadSummary, source: UploadSummary): void {
  target.accepted = target.accepted.concat(source.accepted);
  target.skipped = target.skipped.concat(source.skipped);
  target.rejected = target.rejected.concat(source.rejected);
}

const startTask = useMutation({
  mutationFn: () => api.startTask(taskId),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ["task", taskId] }),
});

async function upload(files: File[]) {
  if (files.length + task.images.length > 100) {
    setUploadError("单个任务最多 100 张图片");
    return;
  }
  const summary = emptyUploadSummary();
  for (const batch of chunk(files, 10)) {
    mergeUploadSummary(summary, await api.uploadTaskImages(taskId, batch));
  }
  setUploadSummary(summary);
  await queryClient.invalidateQueries({ queryKey: ["task", taskId] });
}
```

- [ ] **Step 4: Implement the approved split workspace**

Desktop layout: gallery left and detail right. Mobile layout: gallery first and detail in a full-width drawer. Preserve selected image across SSE refetches by ID. Status colors use accent for pass, danger for fail, muted for queued/not-visible, warning for running/unverifiable, and red outline for execution failure; never communicate status by color alone.

```tsx
<section className="task-workspace">
  <ImageGallery
    images={filteredImages}
    selectedId={selectedImageId}
    onSelect={setSelectedImageId}
  />
  <ImageDetail
    image={task.images.find((image) => image.id === selectedImageId) ?? null}
    onRetry={(imageId) => retryImage.mutate(imageId)}
  />
</section>
```

- [ ] **Step 5: Implement resilient SSE consumption**

`useTaskEvents` opens one EventSource, stores the last event ID in component memory only, invalidates the task query on every terminal image event, and uses exponential reconnect up to 10 seconds. On reconnect, first refetch REST detail, then resume SSE; event loss must not lose state.

```tsx
export function useTaskEvents(taskId: string) {
  const queryClient = useQueryClient();
  useEffect(() => {
    let source: EventSource | null = null;
    let retryMs = 500;
    let closed = false;
    const connect = async () => {
      await queryClient.invalidateQueries({ queryKey: ["task", taskId] });
      if (closed) return;
      source = new EventSource(`/api/tasks/${taskId}/events`);
      source.onmessage = () => queryClient.invalidateQueries({ queryKey: ["task", taskId] });
      source.onopen = () => { retryMs = 500; };
      source.onerror = () => {
        source?.close();
        window.setTimeout(connect, retryMs);
        retryMs = Math.min(10_000, retryMs * 2);
      };
    };
    void connect();
    return () => { closed = true; source?.close(); };
  }, [queryClient, taskId]);
}
```

- [ ] **Step 6: Run tests, accessibility checks, and build**

Run:

```bash
npm --prefix frontend test -- TaskPage.test.tsx
npm --prefix frontend run build
```

Expected: all workspace tests pass and build succeeds without TypeScript errors.

- [ ] **Step 7: Commit the workspace**

```bash
git add frontend/src
git commit -m "feat: add batch inspection workspace"
```

## Task 17: Add history, report status, and static frontend serving

**Files:**
- Create: `frontend/src/features/history/HistoryPage.tsx`
- Create: `frontend/src/features/history/HistoryPage.test.tsx`
- Modify: `frontend/src/app/router.tsx`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_static_app.py`

- [ ] **Step 1: Write failing history and static-serving tests**

Frontend tests verify newest-first tasks, status/count columns, reopening a task, opening the Feishu URL in a new tab, and a clear retry action for failed report export.

Backend test:

```python
def test_spa_fallback_serves_index_for_frontend_routes(built_app_client) -> None:
    response = built_app_client.get("/history")
    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
    assert built_app_client.get("/api/health").headers["content-type"].startswith(
        "application/json"
    )
```

- [ ] **Step 2: Run tests and confirm missing behavior**

Run:

```bash
npm --prefix frontend test -- HistoryPage.test.tsx
backend/.venv/bin/pytest backend/tests/test_static_app.py -v
```

Expected: frontend page and SPA fallback tests fail.

- [ ] **Step 3: Implement history and report actions**

Render task name, created time, template/version, status, four result counts, and report state. “查看任务” routes internally; “打开飞书文档” uses `target="_blank" rel="noreferrer"`; report retry invokes the existing report endpoint and displays progress.

```tsx
<tbody>
  {tasks.map((task) => (
    <tr key={task.id}>
      <td>{task.name}</td>
      <td>{task.template_name} v{task.template_version}</td>
      <td><TaskStatusBadge status={task.status} /></td>
      <td>{task.counts.pass}/{task.counts.fail}/{task.counts.unverifiable}/{task.counts.failed}</td>
      <td><Link to={`/tasks/${task.id}`}>查看任务</Link></td>
      <td>{task.report_url && <a href={task.report_url} target="_blank" rel="noreferrer">打开飞书文档</a>}</td>
    </tr>
  ))}
</tbody>
```

- [ ] **Step 4: Serve the built frontend from FastAPI**

Mount hashed assets under `/assets`. Register API routes before a final SPA fallback route that returns `frontend/dist/index.html` only when the path does not begin with `/api`. If the build is absent in development, omit the static mount and leave API behavior unchanged.

```python
def mount_frontend(app: FastAPI, dist: Path) -> None:
    if not (dist / "index.html").is_file():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        return FileResponse(dist / "index.html")
```

- [ ] **Step 5: Run tests and build**

Run:

```bash
npm --prefix frontend test
npm --prefix frontend run build
backend/.venv/bin/pytest backend/tests/test_static_app.py -v
```

Expected: all frontend tests and static-app tests pass.

- [ ] **Step 6: Commit history and integrated serving**

```bash
git add frontend/src backend/app/main.py backend/tests
git commit -m "feat: add task history and integrated web serving"
```

## Task 18: Add end-to-end coverage, launch scripts, and operator documentation

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/inspection-flow.spec.ts`
- Create: `frontend/e2e/fixtures/one.png`
- Create: `frontend/e2e/fixtures/two.png`
- Create: `frontend/e2e/fixtures/three.png`
- Create: `backend/tests/integration/test_100_image_run.py`
- Create: `backend/tests/integration/test_security.py`
- Create: `backend/tests/e2e_server.py`
- Create: `scripts/dev.sh`
- Create: `scripts/start.sh`
- Create: `.env.example`
- Create: `README.md`

- [ ] **Step 1: Write failing 100-image and security acceptance tests**

The backend integration test creates 100 generated 32×32 PNGs, uses a deterministic fake Ark service with concurrency 3, interrupts after 40 successes, recreates the app, resumes, and asserts 100 unique image/result bindings and accurate counts.

```python
async def test_100_images_resume_without_result_misalignment(integration_harness) -> None:
    task_id = integration_harness.create_task_with_generated_images(100)
    await integration_harness.run_until_success_count(task_id, 40)
    await integration_harness.stop_app()
    await integration_harness.restart_app()
    assert integration_harness.task_status(task_id) == "paused"
    await integration_harness.resume_and_wait(task_id)
    rows = integration_harness.image_result_pairs(task_id)
    assert len(rows) == 100
    assert len({row.image_id for row in rows}) == 100
    assert all(row.result_marker == row.image_sha256 for row in rows)
```

The security test recursively inspects SQLite, logs, API response bodies, and frontend storage probes for sentinel values `ARK_SENTINEL_SECRET`, `FEISHU_SENTINEL_SECRET`, and `TOKEN_SENTINEL_SECRET`; only the fake secret store may contain them.

```python
def test_secrets_exist_only_in_secret_store(security_harness) -> None:
    sentinels = {
        "ARK_SENTINEL_SECRET",
        "FEISHU_SENTINEL_SECRET",
        "TOKEN_SENTINEL_SECRET",
    }
    inspected = security_harness.database_bytes() + security_harness.log_bytes()
    inspected += security_harness.serialized_api_responses()
    for sentinel in sentinels:
        assert sentinel.encode() not in inspected
        assert sentinel in security_harness.secret_store.values()
```

- [ ] **Step 2: Write the Playwright happy path before wiring the mock server**

Generate three committed synthetic fixtures; do not copy user-owned images:

```bash
mkdir -p frontend/e2e/fixtures
backend/.venv/bin/python -c 'from PIL import Image; from pathlib import Path; root=Path("frontend/e2e/fixtures"); [Image.new("RGB", (64, 64), color).save(root / name) for name, color in [("one.png", "white"), ("two.png", "red"), ("three.png", "blue")]]'
```

```ts
// frontend/e2e/inspection-flow.spec.ts
import { expect, test } from "@playwright/test";

test("configures, runs three images, and opens the Feishu report", async ({ page }) => {
  await page.goto("/settings");
  await page.getByLabel("模型 / Endpoint ID").fill("doubao-seed-test");
  await page.getByLabel("API Key").fill("test-key");
  await page.getByRole("button", { name: "测试并保存" }).click();
  await expect(page.getByText("连接测试通过")).toBeVisible();
  await page.goto("/tasks/new");
  await page.getByLabel("任务名称").fill("E2E 巡检");
  await page.getByTestId("image-upload").setInputFiles([
    "e2e/fixtures/one.png",
    "e2e/fixtures/two.png",
    "e2e/fixtures/three.png"
  ]);
  await page.getByRole("button", { name: "开始任务" }).click();
  await expect(page.getByText("飞书报告已生成")).toBeVisible();
  await expect(page.getByRole("link", { name: "打开飞书文档" })).toHaveAttribute(
    "href",
    "https://example.feishu.cn/docx/report-1"
  );
});
```

```ts
// frontend/playwright.config.ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://127.0.0.1:4173" },
  webServer: [
    {
      command: "../backend/.venv/bin/uvicorn tests.e2e_server:app --app-dir ../backend --host 127.0.0.1 --port 8000",
      port: 8000,
      reuseExistingServer: false,
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 4173",
      port: 4173,
      reuseExistingServer: false,
    },
  ],
});
```

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```bash
backend/.venv/bin/pytest backend/tests/integration -v
npm --prefix frontend run e2e
```

Expected: tests fail until the deterministic fake external-service mode and Playwright web server are configured.

- [ ] **Step 4: Add test-only fake external services without production bypasses**

Dependency-inject `InspectionServiceProtocol`, `FeishuOAuthProtocol`, and `FeishuClientProtocol` from the application factory. Test fixtures supply fakes; production construction always requires real keychain configuration. Do not add an environment switch that accepts hard-coded successful results in the production process.

```python
@dataclass(frozen=True)
class ServiceOverrides:
    secrets: SecretStoreProtocol | None = None
    inspection: InspectionServiceProtocol | None = None
    feishu_oauth: FeishuOAuthProtocol | None = None
    feishu_client: FeishuClientProtocol | None = None


def create_app(settings: Settings | None = None, overrides: ServiceOverrides | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    services = build_services(resolved_settings, overrides or ServiceOverrides())
    return build_fastapi_app(resolved_settings, services)
```

`backend/tests/e2e_server.py` creates a temporary data directory, constructs deterministic fake implementations through `ServiceOverrides`, and exports `app = create_app(settings, overrides)`. This module lives under tests and is the only server Playwright may start.

- [ ] **Step 5: Add launch scripts**

`scripts/dev.sh` validates Python and Node versions, starts FastAPI reload mode and Vite, traps exit, and terminates both child processes. `scripts/start.sh` builds the frontend if `dist` is absent, runs Alembic upgrade, starts one Uvicorn worker at `127.0.0.1:8000`, waits for health, and opens `http://127.0.0.1:8000` with macOS `open` when available.

Both scripts begin with `#!/usr/bin/env bash` and `set -euo pipefail`; mark them executable.

```bash
#!/usr/bin/env bash
# scripts/start.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ ! -d "$ROOT/frontend/dist" ]]; then
  npm --prefix "$ROOT/frontend" install
  npm --prefix "$ROOT/frontend" run build
fi
"$ROOT/backend/.venv/bin/alembic" -c "$ROOT/backend/alembic.ini" upgrade head
"$ROOT/backend/.venv/bin/uvicorn" app.main:app \
  --app-dir "$ROOT/backend" --host 127.0.0.1 --port 8000 --workers 1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM
for attempt in {1..50}; do
  if curl --silent --fail http://127.0.0.1:8000/api/health >/dev/null; then
    command -v open >/dev/null && open http://127.0.0.1:8000
    wait "$SERVER_PID"
    exit $?
  fi
  sleep 0.2
done
echo "Seed VLM Inspector failed to become ready" >&2
exit 1
```

- [ ] **Step 6: Write setup and operations documentation**

`README.md` must include:

- Python/Node prerequisites and first install;
- `./scripts/dev.sh` and `./scripts/start.sh`;
- Ark Base URL, model/Endpoint ID, API Key setup and vision test;
- Feishu self-built app creation, callback URL, user OAuth, and functional permissions for Docx create/edit, target-folder access, and Docx image upload;
- local data location and deletion behavior;
- why only visible evidence is evaluated;
- pause/resume/retry/report recovery behavior;
- troubleshooting for Ark 401/403/429, unsupported protocol, unavailable keychain, Feishu reauthorization, permission errors, and media limits;
- manual smoke-test checklist using copies of local sample images without adding those user-owned images to Git.

`.env.example` contains only non-secret path/host/port examples and explicit comments that secrets belong in the UI/keychain.

```dotenv
# Non-secret process settings only. Configure Ark and Feishu secrets in the UI.
# VLM_DATA_DIR defaults to ~/.vlm-inspector; use an absolute path when overriding it.
VLM_HOST=127.0.0.1
VLM_PORT=8000
```

- [ ] **Step 7: Run the complete verification suite**

Run:

```bash
backend/.venv/bin/ruff check backend/app backend/tests
backend/.venv/bin/mypy backend/app
backend/.venv/bin/pytest backend/tests -v
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run e2e
git status --short
```

Expected:

- Ruff and mypy report no errors.
- All backend, frontend, and Playwright tests pass.
- The 100-image restart test reports 100 unique successful bindings.
- The security scan finds sentinels only in the fake secret store.
- `git status --short` shows only intentionally untracked user-owned sample images, never secrets, databases, logs, `.superpowers`, or runtime data.

- [ ] **Step 8: Commit the completed MVP**

```bash
git add README.md .env.example scripts backend frontend
git commit -m "feat: complete local Seed VLM inspection MVP"
```

## Spec coverage matrix

| Approved specification area | Implemented by |
| --- | --- |
| Local React + FastAPI topology and local paths | Tasks 1, 3, 13, 17, 18 |
| Fixed result schema and deterministic verdicts | Tasks 2 and 8 |
| SQLite task/result/report persistence | Tasks 3 and 4 |
| Keychain-only secrets and localhost boundary | Tasks 5, 14, and 18 |
| Image validation, derivatives, and duplicate handling | Tasks 6 and 10 |
| Three versioned starter templates and evidence policy | Tasks 7 and 15 |
| Ark Responses/Chat, model or Endpoint ID, retries | Tasks 8 and 9 |
| One-image-per-call, 1–100 image queue, pause/restart/retry | Tasks 9, 10, and 16 |
| Feishu user OAuth and token refresh | Tasks 11 and 14 |
| New image-rich Docx per task, resume, and no duplicate report | Task 12 |
| Batch gallery plus single-image detail workspace | Tasks 13 and 16 |
| History and report links | Task 17 |
| Error behavior, 100-image acceptance, security, docs | Task 18 |
| Explicit MVP non-goals | Enforced throughout; no multi-user auth, cloud deployment, video, multi-image case inference, custom result schema, Feishu prompt sync, Redis, or Celery tasks are introduced |

## Final implementation review

- [ ] Confirm every requirement and non-goal in the approved design spec maps to a completed task above.
- [ ] Confirm the real Ark smoke test uses one image per request and the selected model/Endpoint.
- [ ] Confirm an all-success batch automatically creates exactly one Feishu document.
- [ ] Confirm a batch with terminal failures stops in `review_required` until explicit user action.
- [ ] Confirm report counts equal database results and every thumbnail matches its immutable image ID.
- [ ] Confirm keychain secrets never appear in frontend persistence, SQLite, logs, API responses, commits, or test artifacts.
- [ ] Confirm existing user-owned image files remain unmodified and untracked unless the user separately requests otherwise.
