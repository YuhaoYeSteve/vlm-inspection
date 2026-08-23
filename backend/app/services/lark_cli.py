import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

from app.services.feishu_client import SpreadsheetRef

REQUIRED_REPORT_SCOPES = frozenset(
    {
        "sheets:spreadsheet:create",
        "sheets:spreadsheet:write_only",
        "sheets:spreadsheet:read",
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
        return cast(dict[str, Any], payload)


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

    async def create_spreadsheet(
        self,
        title: str,
        folder_token: str,
        values: list[list[str]],
        styles: dict[str, Any],
    ) -> SpreadsheetRef:
        args = [
            "sheets",
            "+workbook-create",
            "--title",
            title,
            "--folder-token",
            folder_token,
            "--values",
            "-",
            "--styles",
            json.dumps(styles, ensure_ascii=False, separators=(",", ":")),
            "--as",
            "user",
            "--format",
            "json",
        ]
        payload = await self.runner.run(
            args,
            stdin=json.dumps(values, ensure_ascii=False).encode("utf-8"),
            timeout=90,
        )
        data = self._command_data(payload)
        spreadsheet = data.get("spreadsheet")
        spreadsheet_data = spreadsheet if isinstance(spreadsheet, dict) else data
        token = spreadsheet_data.get("spreadsheet_token") or spreadsheet_data.get("token")
        if not isinstance(token, str) or not token:
            raise LarkCliCommandError("lark_cli_invalid_response")
        raw_url = spreadsheet_data.get("url") or data.get("url")
        url = str(raw_url) if raw_url else f"https://feishu.cn/sheets/{token}"
        sheet_id = self._first_grid_sheet_id(data)
        if sheet_id is None:
            info_payload = await self.runner.run(
                [
                    "sheets",
                    "+workbook-info",
                    "--spreadsheet-token",
                    token,
                    "--as",
                    "user",
                    "--format",
                    "json",
                ],
                timeout=30,
            )
            sheet_id = self._first_grid_sheet_id(self._command_data(info_payload))
        if sheet_id is None:
            raise LarkCliCommandError("lark_cli_invalid_response")
        return SpreadsheetRef(token, url, sheet_id)

    async def set_cell_image(
        self,
        spreadsheet_token: str,
        sheet_id: str,
        cell: str,
        image_path: Path,
        filename: str,
    ) -> None:
        payload = await self.runner.run(
            [
                "sheets",
                "+cells-set-image",
                "--spreadsheet-token",
                spreadsheet_token,
                "--sheet-id",
                sheet_id,
                "--range",
                cell,
                "--image",
                image_path.name,
                "--name",
                filename,
                "--as",
                "user",
                "--format",
                "json",
            ],
            cwd=image_path.parent,
            timeout=90,
        )
        self._command_data(payload)

    @staticmethod
    def _command_data(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("ok") is not True:
            raw_error = payload.get("error")
            error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
            code = str(error.get("subtype") or error.get("type") or "lark_cli_failed")
            raise LarkCliCommandError(code)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LarkCliCommandError("lark_cli_invalid_response")
        return data

    @staticmethod
    def _first_grid_sheet_id(data: dict[str, Any]) -> str | None:
        raw_sheets = data.get("sheets")
        if not isinstance(raw_sheets, list):
            return None
        for raw_sheet in raw_sheets:
            if not isinstance(raw_sheet, dict):
                continue
            if raw_sheet.get("resource_type", "sheet") != "sheet":
                continue
            sheet_id = raw_sheet.get("sheet_id")
            if isinstance(sheet_id, str) and sheet_id:
                return sheet_id
        return None
