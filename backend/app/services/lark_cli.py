import asyncio
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from app.services.feishu_client import BlockRef, DocumentRef, FeishuApiError

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
            stdout, stderr = await asyncio.wait_for(
                process.communicate(stdin),
                timeout=timeout,
            )
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
    def __init__(
        self,
        runner: LarkCliRunnerProtocol,
        destination: FeishuDestination,
    ) -> None:
        self.runner = runner
        self.destination = destination

    async def status(self) -> FeishuConnection:
        if not self.destination.configured:
            return self._disconnected("feishu_destination_missing")
        command = ["auth", "status", "--json", "--verify"]
        try:
            payload = await self.runner.run(command, timeout=15)
            user = (payload.get("identities") or {}).get("user") or {}
            if user.get("tokenStatus") == "needs_refresh":
                payload = await self.runner.run(command, timeout=15)
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
            raw_error = payload.get("error")
            error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
            code = str(error.get("subtype") or error.get("type") or "feishu_api_error")
            raise LarkCliCommandError(code)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LarkCliCommandError("lark_cli_invalid_response")
        return data

    @staticmethod
    def _client_token(operation_key: str) -> str:
        return hashlib.sha256(operation_key.encode()).hexdigest()[:32]
