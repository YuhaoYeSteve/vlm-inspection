# Conversation-Backed Feishu Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the web form OAuth setup with read-only `lark-cli` user authorization, export reports into the configured sibling folder, recover orphaned exports, and regenerate the existing report without rerunning VLM inference.

**Architecture:** FastAPI will call `lark-cli` through an async argv-only runner. A connection provider will project the CLI login state into the existing status API, while a `LarkCliFeishuClient` will preserve the current report builder's idempotent Docx operations. Non-secret destination metadata lives in local environment settings; the React settings page only renders the connected user and destination.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, asyncio subprocesses, SQLAlchemy, pytest, React 19, TypeScript, TanStack Query, Vitest, Testing Library, `lark-cli`.

---

## File map

### Backend

- Create `backend/app/services/lark_cli.py` — safe async command runner, login projection, required-scope checks, and CLI-backed Feishu API adapter.
- Create `backend/tests/services/test_lark_cli.py` — runner, connection, command argument, stdin upload, and error tests.
- Modify `backend/app/core/config.py` — non-secret reference URL, destination token/path, and CLI executable settings.
- Modify `backend/app/schemas.py` — replace editable Feishu settings models with the read-only connection response.
- Modify `backend/app/api/settings.py` — remove Feishu credential read/write endpoints.
- Modify `backend/app/api/feishu_auth.py` — retain only an async read-only status endpoint.
- Modify `backend/app/dependencies.py` — expose the connection provider instead of the legacy OAuth service.
- Modify `backend/app/main.py` — wire the CLI runner, connection provider, report client, and destination token.
- Modify `backend/app/services/reports.py` — await connection readiness and use the injected destination.
- Modify `backend/app/services/runner.py` — finalize drained tasks into review before optional auto-export.
- Modify `backend/app/repositories.py` — recover exporting tasks that have no running export row.
- Modify `backend/tests/conftest.py` and affected API/integration tests — inject a ready connection provider and assert the new contracts.

### Frontend and operations

- Modify `frontend/src/api/types.ts` and `frontend/src/api/client.ts` — use a single read-only Feishu connection type and API call.
- Modify `frontend/src/features/settings/SettingsPage.tsx` — replace credential controls with the status card.
- Modify `frontend/src/features/settings/SettingsPage.test.tsx` — verify account/path/link rendering and absence of credential fields.
- Modify `frontend/src/styles.css` — style the read-only integration details.
- Modify `.env.example` and `README.md` — document conversation authorization and local destination configuration.
- Create local ignored `.env` values during deployment — point the running app at the resolved folder and reference document.

## Task 1: Add the safe lark-cli runner and connection projection

**Files:**
- Create: `backend/app/services/lark_cli.py`
- Create: `backend/tests/services/test_lark_cli.py`
- Modify: `backend/app/core/config.py`

- [ ] **Step 1: Write failing runner and connection tests**

Add these initial tests to `backend/tests/services/test_lark_cli.py`:

```python
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.services.lark_cli import (
    FeishuDestination,
    LarkCliCommandError,
    LarkCliCommandRunner,
    LarkCliConnectionProvider,
)


@dataclass
class FakeRunner:
    payload: dict[str, Any]
    calls: list[list[str]] = field(default_factory=list)

    async def run(
        self,
        args: list[str],
        *,
        stdin: bytes | None = None,
        cwd: Path | None = None,
        timeout: float = 30,
    ) -> dict[str, Any]:
        del stdin, cwd, timeout
        self.calls.append(args)
        return self.payload


@pytest.mark.asyncio
async def test_command_runner_parses_json_without_a_shell() -> None:
    runner = LarkCliCommandRunner(sys.executable)
    payload = await runner.run(["-c", 'print("{\\"ok\\": true, \\"data\\": {\\"value\\": 7}}")'])
    assert payload["data"]["value"] == 7


@pytest.mark.asyncio
async def test_command_runner_raises_stable_error_for_missing_binary() -> None:
    runner = LarkCliCommandRunner("/definitely/missing/lark-cli")
    with pytest.raises(LarkCliCommandError) as raised:
        await runner.run(["auth", "status", "--json"])
    assert raised.value.code == "lark_cli_missing"


@pytest.mark.asyncio
async def test_connection_provider_projects_ready_user_and_destination() -> None:
    runner = FakeRunner(
        {
            "appId": "cli_test",
            "verified": True,
            "identity": "user",
            "identities": {
                "bot": {"appName": "示例应用"},
                "user": {
                    "status": "ready",
                    "userName": "测试用户",
                    "tokenStatus": "valid",
                    "expiresAt": "2026-08-24T01:25:08+08:00",
                    "scope": (
                        "docx:document:create docx:document:write_only "
                        "drive:file:upload offline_access"
                    ),
                },
            },
        }
    )
    provider = LarkCliConnectionProvider(
        runner,
        FeishuDestination(
            parent_token="folder-1",
            path="我的空间 / 示例目录 / 巡检报告",
            reference_url="https://example.feishu.cn/docx/reference",
        ),
    )

    status = await provider.status()

    assert status.configured is True
    assert status.connected is True
    assert status.user_name == "测试用户"
    assert status.app_name == "示例应用"
    assert status.identity == "user"
    assert status.destination_path.endswith("巡检报告")
    assert runner.calls == [["auth", "status", "--json", "--verify"]]


@pytest.mark.asyncio
async def test_connection_provider_reports_missing_scope_without_throwing() -> None:
    runner = FakeRunner(
        {
            "verified": True,
            "identity": "user",
            "identities": {
                "bot": {"appName": "示例应用"},
                "user": {
                    "status": "ready",
                    "userName": "测试用户",
                    "tokenStatus": "valid",
                    "expiresAt": None,
                    "scope": "docx:document:create",
                },
            },
        }
    )
    provider = LarkCliConnectionProvider(
        runner,
        FeishuDestination("folder-1", "目标目录", "https://example/docx/reference"),
    )

    status = await provider.status()

    assert status.connected is False
    assert status.error_code == "feishu_scope_missing"
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_lark_cli.py -v
```

Expected: collection fails because `app.services.lark_cli` does not exist.

- [ ] **Step 3: Add non-secret runtime settings**

Extend `Settings` in `backend/app/core/config.py`:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VLM_", env_file=".env")

    data_dir: Path = Field(default_factory=lambda: Path.home() / ".vlm-inspector")
    host: str = "127.0.0.1"
    port: int = 8000
    lark_cli_path: str = "lark-cli"
    feishu_reference_url: str = ""
    feishu_parent_token: str = ""
    feishu_parent_label: str = ""
```

- [ ] **Step 4: Implement the runner and connection provider**

Create `backend/app/services/lark_cli.py` with these concrete contracts and behavior:

```python
import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


REQUIRED_REPORT_SCOPES = frozenset(
    {
        "docx:document:create",
        "docx:document:write_only",
        "drive:file:upload",
    }
)


class LarkCliCommandError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("lark-cli command failed")
        self.code = code


class LarkCliRunnerProtocol(Protocol):
    async def run(
        self,
        args: list[str],
        *,
        stdin: bytes | None = None,
        cwd: Path | None = None,
        timeout: float = 30,
    ) -> dict[str, Any]: ...


class LarkCliCommandRunner:
    def __init__(self, executable: str = "lark-cli") -> None:
        self.executable = executable

    def _resolved_executable(self) -> str:
        if "/" in self.executable:
            if not Path(self.executable).is_file():
                raise LarkCliCommandError("lark_cli_missing")
            return self.executable
        resolved = shutil.which(self.executable)
        if resolved is None:
            raise LarkCliCommandError("lark_cli_missing")
        return resolved

    async def run(
        self,
        args: list[str],
        *,
        stdin: bytes | None = None,
        cwd: Path | None = None,
        timeout: float = 30,
    ) -> dict[str, Any]:
        executable = self._resolved_executable()
        env = {
            **os.environ,
            "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
            "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
        }
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *args,
                cwd=None if cwd is None else str(cwd),
                env=env,
                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise LarkCliCommandError("lark_cli_missing") from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(stdin), timeout=timeout)
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise LarkCliCommandError("feishu_command_timeout") from exc
        text = (stdout if stdout.strip() else stderr).decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LarkCliCommandError("lark_cli_invalid_response") from exc
        if process.returncode != 0 or payload.get("ok") is False:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            code = str(error.get("subtype") or error.get("type") or "lark_cli_failed")
            raise LarkCliCommandError(code)
        return payload


@dataclass(frozen=True)
class FeishuDestination:
    parent_token: str
    path: str
    reference_url: str

    @property
    def configured(self) -> bool:
        return bool(self.parent_token and self.path and self.reference_url)


@dataclass(frozen=True)
class FeishuConnection:
    configured: bool
    connected: bool
    user_name: str | None
    app_name: str | None
    identity: str | None
    token_status: str | None
    expires_at: datetime | None
    destination_path: str
    reference_url: str
    error_code: str | None = None


class FeishuConnectionProviderProtocol(Protocol):
    async def status(self) -> FeishuConnection: ...


class LarkCliConnectionProvider:
    def __init__(self, runner: LarkCliRunnerProtocol, destination: FeishuDestination) -> None:
        self.runner = runner
        self.destination = destination

    async def status(self) -> FeishuConnection:
        if not self.destination.configured:
            return self._disconnected("feishu_destination_missing")
        try:
            payload = await self.runner.run(
                ["auth", "status", "--json", "--verify"],
                timeout=15,
            )
        except LarkCliCommandError as exc:
            return self._disconnected(exc.code)
        identities = payload.get("identities") or {}
        user = identities.get("user") or {}
        bot = identities.get("bot") or {}
        scopes = set(str(user.get("scope") or "").split())
        error_code = None
        if not REQUIRED_REPORT_SCOPES.issubset(scopes):
            error_code = "feishu_scope_missing"
        connected = bool(
            payload.get("verified") is True
            and payload.get("identity") == "user"
            and user.get("status") == "ready"
            and user.get("tokenStatus") == "valid"
            and error_code is None
        )
        expires_text = user.get("expiresAt")
        expires_at = datetime.fromisoformat(expires_text) if expires_text else None
        return FeishuConnection(
            configured=True,
            connected=connected,
            user_name=user.get("userName"),
            app_name=bot.get("appName"),
            identity=payload.get("identity"),
            token_status=user.get("tokenStatus"),
            expires_at=expires_at,
            destination_path=self.destination.path,
            reference_url=self.destination.reference_url,
            error_code=error_code or (None if connected else "feishu_user_not_connected"),
        )

    def _disconnected(self, error_code: str) -> FeishuConnection:
        return FeishuConnection(
            configured=self.destination.configured,
            connected=False,
            user_name=None,
            app_name=None,
            identity=None,
            token_status=None,
            expires_at=None,
            destination_path=self.destination.path,
            reference_url=self.destination.reference_url,
            error_code=error_code,
        )
```

- [ ] **Step 5: Run the runner tests and verify GREEN**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_lark_cli.py -v
backend/.venv/bin/ruff check backend/app/services/lark_cli.py backend/tests/services/test_lark_cli.py
```

Expected: all new tests pass and Ruff reports no errors.

- [ ] **Step 6: Commit the runner foundation**

```bash
git add backend/app/core/config.py backend/app/services/lark_cli.py backend/tests/services/test_lark_cli.py
git commit -m "feat: read Feishu authorization from lark cli"
```

## Task 2: Implement the CLI-backed report client

**Files:**
- Modify: `backend/app/services/lark_cli.py`
- Modify: `backend/tests/services/test_lark_cli.py`

- [ ] **Step 1: Add failing command-contract tests**

Append to `backend/tests/services/test_lark_cli.py`:

```python
from app.services.lark_cli import LarkCliFeishuClient


@dataclass
class QueueRunner:
    payloads: list[dict[str, Any]]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def run(
        self,
        args: list[str],
        *,
        stdin: bytes | None = None,
        cwd: Path | None = None,
        timeout: float = 30,
    ) -> dict[str, Any]:
        self.calls.append({"args": args, "stdin": stdin, "cwd": cwd, "timeout": timeout})
        return self.payloads.pop(0)


@pytest.mark.asyncio
async def test_report_client_creates_document_with_user_identity() -> None:
    runner = QueueRunner(
        [
            {
                "ok": True,
                "identity": "user",
                "data": {
                    "document": {
                        "document_id": "doc-1",
                        "url": "https://example.feishu.cn/docx/doc-1",
                    }
                },
            }
        ]
    )
    client = LarkCliFeishuClient(runner)

    document = await client.create_document("巡检报告", "folder-1", "export-1")

    assert document.id == "doc-1"
    call = runner.calls[0]["args"]
    assert call[:4] == ["api", "POST", "/open-apis/docx/v1/documents", "--as"]
    assert call[4] == "user"
    assert "folder-1" in call


@pytest.mark.asyncio
async def test_report_client_uploads_image_bytes_through_stdin() -> None:
    runner = QueueRunner(
        [{"ok": True, "identity": "user", "data": {"file_token": "file-1"}}]
    )
    client = LarkCliFeishuClient(runner)

    token = await client.upload_docx_image("block-1", "one.jpg", b"jpeg-bytes")

    assert token == "file-1"
    call = runner.calls[0]
    assert call["stdin"] == b"jpeg-bytes"
    assert "file=-" in call["args"]
    assert "docx_image" in call["args"]


@pytest.mark.asyncio
async def test_report_client_rejects_failed_cli_envelope() -> None:
    runner = QueueRunner(
        [
            {
                "ok": False,
                "identity": "user",
                "error": {"type": "authorization", "subtype": "missing_scope"},
            }
        ]
    )
    client = LarkCliFeishuClient(runner)

    with pytest.raises(LarkCliCommandError) as raised:
        await client.create_document("巡检报告", "folder-1", "export-1")
    assert raised.value.code == "missing_scope"
```

- [ ] **Step 2: Run the client tests and verify RED**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_lark_cli.py -k report_client -v
```

Expected: import fails because `LarkCliFeishuClient` is not defined.

- [ ] **Step 3: Implement the CLI report adapter**

Append a `LarkCliFeishuClient` implementation to `backend/app/services/lark_cli.py`. Reuse `DocumentRef`, `BlockRef`, and `FeishuApiError` from `app.services.feishu_client`:

```python
from app.services.feishu_client import BlockRef, DocumentRef, FeishuApiError


class LarkCliFeishuClient:
    def __init__(self, runner: LarkCliRunnerProtocol) -> None:
        self.runner = runner

    async def create_document(
        self,
        title: str,
        folder_token: str,
        client_token: str,
    ) -> DocumentRef:
        data = await self._api(
            "POST",
            "/open-apis/docx/v1/documents",
            params={"client_token": client_token},
            body={"title": title, "folder_token": folder_token},
        )
        document = data["document"]
        document_id = str(document["document_id"])
        url = str(document.get("url") or f"https://feishu.cn/docx/{document_id}")
        return DocumentRef(document_id, url)

    async def create_blocks(
        self,
        document_id: str,
        parent_block_id: str,
        blocks: list[dict[str, Any]],
        operation_key: str,
    ) -> list[BlockRef]:
        data = await self._api(
            "POST",
            f"/open-apis/docx/v1/documents/{document_id}/blocks/{parent_block_id}/children",
            params={
                "document_revision_id": "-1",
                "client_token": self._client_token(operation_key),
            },
            body={"children": blocks, "index": -1},
        )
        return [BlockRef(str(block["block_id"])) for block in data["children"]]

    async def create_image_block(
        self,
        document_id: str,
        parent_block_id: str,
        operation_key: str,
    ) -> BlockRef:
        blocks = await self.create_blocks(
            document_id,
            parent_block_id,
            [{"block_type": 27, "image": {}}],
            operation_key,
        )
        if not blocks:
            raise FeishuApiError("image_block_missing")
        return blocks[0]

    async def upload_docx_image(self, block_id: str, filename: str, content: bytes) -> str:
        data = await self._api(
            "POST",
            "/open-apis/drive/v1/medias/upload_all",
            body={
                "file_name": filename,
                "parent_type": "docx_image",
                "parent_node": block_id,
                "size": str(len(content)),
            },
            file_stdin=content,
            timeout=90,
        )
        return str(data["file_token"])

    async def _api(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        file_stdin: bytes | None = None,
        timeout: float = 60,
    ) -> dict[str, Any]:
        args = ["api", method, path, "--as", "user", "--format", "json"]
        if params is not None:
            args.extend(["--params", json.dumps(params, ensure_ascii=False)])
        if body is not None:
            args.extend(["--data", json.dumps(body, ensure_ascii=False)])
        if file_stdin is not None:
            args.extend(["--file", "file=-"])
        payload = await self.runner.run(args, stdin=file_stdin, timeout=timeout)
        if payload.get("ok") is not True:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            code = str(error.get("subtype") or error.get("type") or "feishu_api_error")
            raise LarkCliCommandError(code)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LarkCliCommandError("lark_cli_invalid_response")
        return data

    @staticmethod
    def _client_token(operation_key: str) -> str:
        import hashlib

        return hashlib.sha256(operation_key.encode()).hexdigest()[:32]
```

Add `LarkCliCommandError` to the caught report export errors in `ReportBuilder.export_prepared` during Task 3 so failures become persisted report failures.

- [ ] **Step 4: Run the complete adapter test file**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_lark_cli.py -v
backend/.venv/bin/ruff check backend/app/services/lark_cli.py backend/tests/services/test_lark_cli.py
```

Expected: all tests pass and Ruff reports no errors.

- [ ] **Step 5: Commit the report adapter**

```bash
git add backend/app/services/lark_cli.py backend/tests/services/test_lark_cli.py
git commit -m "feat: export Feishu reports through lark cli"
```

## Task 3: Wire the read-only authorization API and report coordinator

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/api/settings.py`
- Modify: `backend/app/api/feishu_auth.py`
- Modify: `backend/app/dependencies.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/reports.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/api/test_settings.py`
- Modify: `backend/tests/api/test_feishu_auth.py`
- Modify: `backend/tests/api/test_reports.py`
- Modify: `backend/tests/integration/test_security.py`

- [ ] **Step 1: Replace legacy OAuth API tests with failing read-only status tests**

Rewrite `backend/tests/api/test_feishu_auth.py`:

```python
def test_feishu_status_exposes_conversation_identity_and_destination(app_harness) -> None:
    response = app_harness.client.get("/api/auth/feishu/status")

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "connected": True,
        "user_name": "测试用户",
        "app_name": "示例应用",
        "identity": "user",
        "token_status": "valid",
        "expires_at": "2030-01-01T00:00:00Z",
        "destination_path": "测试报告目录",
        "reference_url": "https://example.feishu.cn/docx/reference",
        "error_code": None,
    }


def test_legacy_web_oauth_routes_are_not_exposed(app_harness) -> None:
    assert app_harness.client.get("/api/auth/feishu/start").status_code == 404
    assert app_harness.client.delete("/api/auth/feishu/disconnect").status_code == 404
```

Add this test to `backend/tests/api/test_settings.py`:

```python
def test_feishu_credentials_cannot_be_written_through_settings(app_harness) -> None:
    response = app_harness.client.put(
        "/api/settings/feishu",
        json={"app_id": "cli", "app_secret": "secret", "folder_token": "folder"},
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run the API tests and verify RED**

Run:

```bash
backend/.venv/bin/pytest backend/tests/api/test_feishu_auth.py backend/tests/api/test_settings.py -v
```

Expected: the status response lacks the new fields and legacy routes remain available.

- [ ] **Step 3: Replace the Feishu schemas and routes**

Replace legacy Feishu settings/start models in `backend/app/schemas.py` with:

```python
class FeishuAuthStatus(BaseModel):
    configured: bool
    connected: bool
    user_name: str | None
    app_name: str | None
    identity: str | None
    token_status: str | None
    expires_at: datetime | None
    destination_path: str
    reference_url: str
    error_code: str | None
```

Remove the Feishu GET/PUT handlers and their imports from `backend/app/api/settings.py`. Rewrite `backend/app/api/feishu_auth.py` as:

```python
from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies import get_feishu_connection_provider
from app.schemas import FeishuAuthStatus
from app.services.lark_cli import FeishuConnectionProviderProtocol

router = APIRouter(prefix="/api/auth/feishu", tags=["feishu-auth"])


@router.get("/status", response_model=FeishuAuthStatus)
async def authorization_status(
    provider: Annotated[
        FeishuConnectionProviderProtocol,
        Depends(get_feishu_connection_provider),
    ],
) -> FeishuAuthStatus:
    return FeishuAuthStatus.model_validate(await provider.status(), from_attributes=True)
```

Replace `get_feishu_oauth` in `backend/app/dependencies.py` with:

```python
def get_feishu_connection_provider(request: Request) -> FeishuConnectionProviderProtocol:
    return request.app.state.feishu_connection_provider
```

- [ ] **Step 4: Make report readiness asynchronous and inject the destination**

Update `ReportBuilder.__init__` in `backend/app/services/reports.py` to accept `folder_token: str`, store it, and replace `_folder_token` with:

```python
def _folder_token(self) -> str:
    if not self.folder_token:
        raise ReportNotReady("Feishu report destination is not configured")
    return self.folder_token
```

Change `ReportCoordinator` to depend on `FeishuConnectionProviderProtocol` and await readiness:

```python
async def is_ready(self) -> bool:
    status = await self.connection_provider.status()
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
```

Import and catch `LarkCliCommandError` in `ReportBuilder.export_prepared`.

- [ ] **Step 5: Wire production and test dependencies**

In `backend/app/main.py`:

```python
def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    secret_store: SecretStoreProtocol | None = None,
    inspection_service: InspectionServiceProtocol | None = None,
    lark_cli_runner: LarkCliRunnerProtocol | None = None,
    feishu_connection_provider: FeishuConnectionProviderProtocol | None = None,
    feishu_client: FeishuClientProtocol | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    # existing database, secret-store, event, and inspection setup remains above

destination = FeishuDestination(
    parent_token=resolved_settings.feishu_parent_token,
    path=resolved_settings.feishu_parent_label,
    reference_url=resolved_settings.feishu_reference_url,
)
resolved_lark_runner = lark_cli_runner or LarkCliCommandRunner(resolved_settings.lark_cli_path)
resolved_connection_provider = (
    feishu_connection_provider
    or LarkCliConnectionProvider(resolved_lark_runner, destination)
)
resolved_feishu_client = feishu_client or LarkCliFeishuClient(resolved_lark_runner)
report_builder = ReportBuilder(
    sessions,
    resolved_feishu_client,
    folder_token=destination.parent_token,
)
report_coordinator = ReportCoordinator(report_builder, resolved_connection_provider, events)
```

Expose `feishu_connection_provider` on `app.state`, remove the legacy OAuth construction, and keep optional `feishu_client` plus new `lark_cli_runner` and `feishu_connection_provider` injection arguments for tests.

In `backend/tests/conftest.py`, add a fake ready provider:

```python
from datetime import UTC, datetime
from app.services.lark_cli import FeishuConnection


class FakeFeishuConnectionProvider:
    async def status(self) -> FeishuConnection:
        return FeishuConnection(
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
```

Build test `Settings` with `feishu_parent_token="folder-test"`, inject the fake provider into `create_app`, and remove the fake OAuth HTTP client from `AppHarness`.

- [ ] **Step 6: Update report/security test setup for read-only auth**

Remove `PUT /api/settings/feishu` and Feishu token setup from `configure_services` in `backend/tests/api/test_reports.py`; the harness's ready provider supplies authorization. Update `backend/tests/integration/test_security.py` so it asserts an attempted Feishu credential write returns 404 and that the sentinel is absent from SQLite, logs, and all read APIs.

- [ ] **Step 7: Run API and report tests**

Run:

```bash
backend/.venv/bin/pytest \
  backend/tests/api/test_feishu_auth.py \
  backend/tests/api/test_settings.py \
  backend/tests/api/test_reports.py \
  backend/tests/services/test_reports.py \
  backend/tests/integration/test_security.py -v
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit backend integration**

```bash
git add backend/app backend/tests
git commit -m "refactor: use conversation Feishu authorization"
```

## Task 4: Repair report state transitions and startup recovery

**Files:**
- Modify: `backend/app/services/runner.py`
- Modify: `backend/app/repositories.py`
- Modify: `backend/tests/services/test_runner.py`
- Modify: `backend/tests/services/test_reports.py`
- Modify: `backend/tests/integration/test_100_image_run.py`

- [ ] **Step 1: Write failing state-machine tests**

Add these cases to `backend/tests/services/test_runner.py`:

```python
@pytest.mark.asyncio
async def test_successful_task_waits_for_report_when_auto_export_is_unavailable(tmp_path) -> None:
    fixture = make_task_fixture(tmp_path, image_count=1)

    async def unavailable(_task_id: str) -> None:
        return None

    runner = TaskRunner(
        fixture.sessions,
        FakeInspector(),
        concurrency=1,
        report_callback=unavailable,
    )

    await runner.run_task(fixture.task_id)

    assert fixture.task_status() == TaskStatus.REVIEW_REQUIRED


@pytest.mark.asyncio
async def test_successful_task_enters_exporting_only_after_export_is_prepared(tmp_path) -> None:
    fixture = make_task_fixture(tmp_path, image_count=1)

    async def prepared(task_id: str) -> str:
        with fixture.sessions() as session:  # type: ignore[operator]
            TaskRepository(session).set_task_status(task_id, TaskStatus.EXPORTING)
            session.commit()
        return "export-1"

    runner = TaskRunner(
        fixture.sessions,
        FakeInspector(),
        concurrency=1,
        report_callback=prepared,
    )

    await runner.run_task(fixture.task_id)

    assert fixture.task_status() == TaskStatus.EXPORTING
```

Add a repository recovery test to `backend/tests/services/test_reports.py` that creates a task in `EXPORTING` without a report row, runs `recover_interrupted_exports`, and expects `REVIEW_REQUIRED`.

- [ ] **Step 2: Run the state tests and verify RED**

Run:

```bash
backend/.venv/bin/pytest \
  backend/tests/services/test_runner.py \
  backend/tests/services/test_reports.py -k 'successful_task or interrupted' -v
```

Expected: the unavailable callback leaves the task exporting and orphan recovery returns zero.

- [ ] **Step 3: Finalize drained tasks into review before auto-export**

Change `_finalize_if_drained` in `backend/app/services/runner.py` to return `bool | None`, where `True` means every image succeeded:

```python
def _finalize_if_drained(self, task_id: str) -> bool | None:
    with self.sessions() as session:
        repo = TaskRepository(session)
        task = repo.get_task(task_id)
        if TaskStatus(task.status) is not TaskStatus.RUNNING:
            return None
        statuses = list(
            session.scalars(
                select(TaskImage.status)
                .where(TaskImage.task_id == task_id)
                .order_by(TaskImage.position)
            )
        )
        if any(
            status in {ImageStatus.QUEUED.value, ImageStatus.RUNNING.value}
            for status in statuses
        ):
            return None
        all_succeeded = bool(statuses) and all(
            status == ImageStatus.SUCCEEDED.value for status in statuses
        )
        repo.set_task_status(task_id, TaskStatus.REVIEW_REQUIRED)
        session.commit()
        return all_succeeded
```

Update `run_task`:

```python
all_succeeded = self._finalize_if_drained(task_id)
if all_succeeded is not None:
    await self.events.publish(
        task_id,
        {
            "type": "task.updated",
            "task_id": task_id,
            "status": TaskStatus.REVIEW_REQUIRED.value,
        },
    )
    if all_succeeded and self.report_callback is not None:
        export_id = await self.report_callback(task_id)
        if export_id is not None:
            await self.events.publish(
                task_id,
                {
                    "type": "task.updated",
                    "task_id": task_id,
                    "status": TaskStatus.EXPORTING.value,
                },
            )
```

Update existing runner and 100-image assertions from `exporting` to `review_required` when no report callback is injected.

- [ ] **Step 4: Recover orphan exporting tasks**

Extend `TaskRepository.recover_interrupted_exports` after marking running exports failed:

```python
recovered_task_ids: set[str] = set()
exports = self.session.scalars(
    select(ReportExport).where(ReportExport.status == ReportStatus.RUNNING.value)
).all()
for export in exports:
    export.status = ReportStatus.FAILED.value
    export.error_code = "report_interrupted"
    task = self.get_task(export.task_id)
    if TaskStatus(task.status) is TaskStatus.EXPORTING:
        task.status = TaskStatus.REVIEW_REQUIRED.value
    recovered_task_ids.add(task.id)

exporting_tasks = self.session.scalars(
    select(InspectionTask).where(InspectionTask.status == TaskStatus.EXPORTING.value)
).all()
for task in exporting_tasks:
    active = self.get_active_export(task.id)
    if active is None or active.status != ReportStatus.RUNNING.value:
        task.status = TaskStatus.REVIEW_REQUIRED.value
        recovered_task_ids.add(task.id)
return len(recovered_task_ids)
```

Return the combined number of interrupted exports and orphan tasks, without double-counting tasks already transitioned while handling a running export.

- [ ] **Step 5: Run state, report, and restart tests**

Run:

```bash
backend/.venv/bin/pytest \
  backend/tests/services/test_runner.py \
  backend/tests/services/test_reports.py \
  backend/tests/integration/test_100_image_run.py -v
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the state repair**

```bash
git add backend/app/services/runner.py backend/app/repositories.py backend/tests
git commit -m "fix: recover reports waiting for Feishu authorization"
```

## Task 5: Replace the Feishu settings form with a read-only status card

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/features/settings/SettingsPage.tsx`
- Modify: `frontend/src/features/settings/SettingsPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing read-only UI tests**

Replace the Feishu handlers and authorization test in `SettingsPage.test.tsx` with:

```typescript
const feishuStatus = {
  configured: true,
  connected: true,
  user_name: "测试用户",
  app_name: "示例应用",
  identity: "user",
  token_status: "valid",
  expires_at: "2030-01-01T00:00:00Z",
  destination_path: "我的空间 / 示例目录 / 巡检报告",
  reference_url: "https://example.feishu.cn/docx/reference",
  error_code: null,
};

test("renders Feishu account and destination without credential inputs", async () => {
  useDefaultHandlers();
  renderSettings();

  expect(await screen.findByText("测试用户")).toBeInTheDocument();
  expect(screen.getByText("示例应用")).toBeInTheDocument();
  expect(screen.getByText(feishuStatus.destination_path)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "打开参考文档" })).toHaveAttribute(
    "href",
    feishuStatus.reference_url,
  );
  expect(screen.queryByText("App ID")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("App Secret")).not.toBeInTheDocument();
  expect(screen.queryByText("报告文件夹 Token")).not.toBeInTheDocument();
  expect(screen.queryByText("OAuth 回调地址")).not.toBeInTheDocument();
});

test("refreshes conversation authorization status", async () => {
  useDefaultHandlers();
  const user = userEvent.setup();
  renderSettings();
  await screen.findByText("测试用户");

  await user.click(screen.getByRole("button", { name: "刷新状态" }));

  await waitFor(() => expect(screen.getByText("已连接")).toBeInTheDocument());
});
```

Make `useDefaultHandlers` serve only `GET /api/auth/feishu/status` with `feishuStatus`; remove Feishu settings PUT, OAuth start, and disconnect handlers.
Update the existing secret-state assertion to expect one “已存入系统钥匙串” marker, because only the Ark API key remains editable.

- [ ] **Step 2: Run the settings tests and verify RED**

Run:

```bash
npm --prefix frontend test -- SettingsPage.test.tsx
```

Expected: account/path text is missing and credential fields are still rendered.

- [ ] **Step 3: Add the frontend connection type and simplify the client**

Replace `FeishuSettings` in `frontend/src/api/types.ts` with:

```typescript
export interface FeishuConnection {
  configured: boolean;
  connected: boolean;
  user_name: string | null;
  app_name: string | null;
  identity: string | null;
  token_status: string | null;
  expires_at: string | null;
  destination_path: string;
  reference_url: string;
  error_code: string | null;
}
```

In `frontend/src/api/client.ts`, keep only:

```typescript
getFeishuStatus = () =>
  this.request<FeishuConnection>("/api/auth/feishu/status");
```

Remove `getFeishuSettings`, `saveFeishuSettings`, `startFeishuAuth`, and `disconnectFeishu`.

- [ ] **Step 4: Render the read-only integration card**

In `SettingsPage.tsx`, remove `useSearchParams`, all Feishu form state/mutations, and the credential fields. Use the query's `refetch` for the only action. The Feishu panel body must follow this structure:

```tsx
<div className="connection-status">
  <span>授权状态</span>
  <strong className={feishuStatus.data?.connected ? "is-connected" : ""}>
    {feishuStatus.data?.connected ? "已连接" : "未连接"}
  </strong>
</div>
<dl className="integration-details">
  <div><dt>已连接账号</dt><dd>{feishuStatus.data?.user_name ?? "—"}</dd></div>
  <div><dt>授权方式</dt><dd>Codex 对话授权</dd></div>
  <div><dt>飞书应用</dt><dd>{feishuStatus.data?.app_name ?? "—"}</dd></div>
  <div className="integration-detail-wide">
    <dt>报告位置</dt>
    <dd>{feishuStatus.data?.destination_path || "未配置"}</dd>
  </div>
</dl>
<div className="form-actions split-actions">
  <button
    className="button quiet-button"
    type="button"
    disabled={feishuStatus.isFetching}
    onClick={() => void feishuStatus.refetch()}
  >
    <ArrowClockwise size={18} />
    {feishuStatus.isFetching ? "刷新中..." : "刷新状态"}
  </button>
  {feishuStatus.data?.reference_url && (
    <a
      className="button quiet-button"
      href={feishuStatus.data.reference_url}
      target="_blank"
      rel="noreferrer"
    >
      打开参考文档 <ArrowSquareOut size={16} />
    </a>
  )}
</div>
{!feishuStatus.data?.connected && (
  <p className="integration-hint">请在 Codex 对话中输入“授权飞书”，完成后刷新状态。</p>
)}
```

Keep the existing Ark API Key form unchanged.

- [ ] **Step 5: Style the details list**

Add focused styles in `frontend/src/styles.css`:

```css
.integration-details {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin: 0;
  overflow: hidden;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: 8px;
}

.integration-details > div {
  min-width: 0;
  padding: 14px;
  background: var(--surface);
}

.integration-details dt {
  color: var(--ink-muted);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.integration-details dd {
  margin: 5px 0 0;
  overflow-wrap: anywhere;
  font-size: 13px;
  font-weight: 700;
}

.integration-detail-wide {
  grid-column: 1 / -1;
}

.integration-hint {
  margin: 14px 0 0;
  color: var(--warning);
  font-size: 12px;
  line-height: 1.6;
}
```

Add a mobile rule that collapses `.integration-details` to one column and resets `.integration-detail-wide` to auto placement.

- [ ] **Step 6: Run frontend tests and build**

Run:

```bash
npm --prefix frontend test -- SettingsPage.test.tsx
npm --prefix frontend run build
```

Expected: settings tests pass and the TypeScript/Vite build exits 0.

- [ ] **Step 7: Commit the read-only settings card**

```bash
git add frontend/src
git commit -m "feat: show read-only Feishu connection status"
```

## Task 6: Document, configure, verify, and recover the real report

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Local ignored file: `.env`

- [ ] **Step 1: Update operational documentation**

Replace the README's manual Feishu application setup section with these operational facts:

```markdown
## 飞书报告授权

巡检台复用本机 `lark-cli` 的用户身份，不在网页中接收 App ID、App Secret 或 OAuth Token。

1. 在 Codex 对话中说“授权飞书”。
2. 完成二维码授权后，由 Codex 完成设备码登录。
3. 打开巡检台“设置”，确认账号、飞书应用和报告目录均正确。
4. 授权过期或权限不足时，回到 Codex 对话重新授权，然后点击“刷新状态”。

报告目录由本机 `.env` 中的非密钥配置指定。设置页只展示，不提供编辑入口。
```

Append blank documented settings to `.env.example`:

```dotenv
# Feishu report destination. Configure locally through the Codex conversation;
# the web UI is intentionally read-only.
VLM_LARK_CLI_PATH=lark-cli
VLM_FEISHU_REFERENCE_URL=
VLM_FEISHU_PARENT_TOKEN=
VLM_FEISHU_PARENT_LABEL=
```

- [ ] **Step 2: Run the complete automated verification suite**

Run:

```bash
backend/.venv/bin/ruff check backend/app backend/tests
backend/.venv/bin/mypy backend/app
backend/.venv/bin/pytest backend/tests -v
npm --prefix frontend test
npm --prefix frontend run build
```

Expected: every command exits 0 with no test failures, type errors, lint errors, or build errors.

- [ ] **Step 3: Commit docs after automated verification**

```bash
git add .env.example README.md
git commit -m "docs: explain conversation Feishu authorization"
```

- [ ] **Step 4: Write the resolved local destination without exposing credentials**

Create or update the ignored `.env` using `apply_patch`, preserving any existing entries. Set `VLM_LARK_CLI_PATH` to `/opt/homebrew/bin/lark-cli` and set the reference URL, parent token, and display label to the three values already resolved and verified during this conversation. Their tenant-specific literals are intentionally omitted from this tracked plan.

```dotenv
VLM_LARK_CLI_PATH=/opt/homebrew/bin/lark-cli
```

Do not stage `.env` or the generated QR images.

- [ ] **Step 5: Restart the local application and verify read-only status**

Stop only the existing `scripts/start.sh` process for this worktree, then start the updated app in a PTY with:

```bash
VLM_NO_OPEN=1 ./scripts/start.sh
```

Verify:

```bash
curl -sS http://127.0.0.1:8000/api/auth/feishu/status | python3 -m json.tool
curl -sS http://127.0.0.1:8000/api/tasks | python3 -m json.tool
```

Expected: the status names the connected user and target path; the former orphan task is now `review_required` rather than `exporting`.

- [ ] **Step 6: Trigger the existing report without rerunning inference**

Record the current inference attempt count, submit the existing task's report, and poll:

```bash
sqlite3 "$HOME/.vlm-inspector/app.db" 'select count(*) from inference_attempts;'
curl -sS -X POST \
  -H 'Content-Type: application/json' \
  -d '{"include_failures":false}' \
  http://127.0.0.1:8000/api/tasks/25e7e2d6-70ff-4388-8957-66ab2beb453a/report
curl -sS http://127.0.0.1:8000/api/tasks/25e7e2d6-70ff-4388-8957-66ab2beb453a \
  | python3 -m json.tool
```

Expected: the task reaches `completed`, `report_status` is `succeeded`, `report_url` is non-null, and the inference attempt count remains 12.

- [ ] **Step 7: Verify the created document and destination**

Use the returned report URL with read-only `lark-cli drive +inspect --as user` and `lark-cli docs +fetch --as user`. Confirm:

- the document exists in the configured parent directory;
- the report counts are 10 pass, 2 fail, 0 unverifiable, and 0 execution failures;
- all 12 image sections exist and uploaded images are present;
- the local task exposes the same report URL.

- [ ] **Step 8: Inspect the final diff and working tree**

Run:

```bash
git status --short
git diff --check main...HEAD
git log --oneline -8
```

Expected: only the three generated QR PNGs and ignored `.env` remain outside commits; source and documentation changes are committed with no whitespace errors.
