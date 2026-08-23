import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.services.feishu_client import SpreadsheetRef
from app.services.lark_cli import (
    FeishuDestination,
    LarkCliCommandError,
    LarkCliCommandRunner,
    LarkCliConnectionProvider,
    LarkCliFeishuClient,
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

    payload = await runner.run(
        ["-c", 'print("{\\"ok\\": true, \\"data\\": {\\"value\\": 7}}")']
    )

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
                        "sheets:spreadsheet:create sheets:spreadsheet:write_only "
                        "sheets:spreadsheet:read drive:file:upload offline_access"
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
                    "scope": "sheets:spreadsheet:read drive:file:upload",
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
async def test_connection_provider_rechecks_status_after_cli_refreshes_token() -> None:
    base_user = {
        "status": "ready",
        "userName": "测试用户",
        "expiresAt": "2026-08-23T12:57:16+08:00",
        "scope": (
            "sheets:spreadsheet:create sheets:spreadsheet:write_only "
            "sheets:spreadsheet:read drive:file:upload offline_access"
        ),
    }
    runner = QueueRunner(
        [
            {
                "verified": True,
                "identity": "user",
                "identities": {
                    "bot": {"appName": "示例应用"},
                    "user": {**base_user, "tokenStatus": "needs_refresh"},
                },
            },
            {
                "verified": True,
                "identity": "user",
                "identities": {
                    "bot": {"appName": "示例应用"},
                    "user": {**base_user, "tokenStatus": "valid"},
                },
            },
        ]
    )
    provider = LarkCliConnectionProvider(
        runner,
        FeishuDestination("folder-1", "目标目录", "https://example/docx/reference"),
    )

    status = await provider.status()

    assert status.connected is True
    assert status.token_status == "valid"
    assert len(runner.calls) == 2


@pytest.mark.asyncio
async def test_report_client_creates_spreadsheet_with_values_styles_and_sheet_id() -> None:
    runner = QueueRunner(
        [
            {
                "ok": True,
                "identity": "user",
                "data": {
                    "spreadsheet": {
                        "spreadsheet_token": "spreadsheet-1",
                        "url": "https://example.feishu.cn/sheets/spreadsheet-1",
                    },
                    "sheets": [{"sheet_id": "sheet-1", "title": "Sheet1"}],
                },
            }
        ]
    )
    client = LarkCliFeishuClient(runner)
    values = [["巡检项", "安保"], ["巡检图片", ""]]
    styles = {
        "styles": [
            {
                "name": "Sheet1",
                "cell_styles": [{"range": "A1:B1", "font_weight": "bold"}],
            }
        ]
    }

    spreadsheet = await client.create_spreadsheet(
        "巡检报告",
        "folder-1",
        values,
        styles,
    )

    assert spreadsheet == SpreadsheetRef(
        "spreadsheet-1",
        "https://example.feishu.cn/sheets/spreadsheet-1",
        "sheet-1",
    )
    call = runner.calls[0]
    assert call["args"][:3] == ["sheets", "+workbook-create", "--title"]
    assert call["args"][3] == "巡检报告"
    assert call["args"][4:6] == ["--folder-token", "folder-1"]
    assert call["args"][call["args"].index("--values") + 1] == "-"
    assert json.loads(call["stdin"].decode()) == values
    styles_arg = call["args"][call["args"].index("--styles") + 1]
    assert json.loads(styles_arg) == styles
    assert call["timeout"] == 90


@pytest.mark.asyncio
async def test_report_client_fetches_first_sheet_when_create_response_omits_it() -> None:
    runner = QueueRunner(
        [
            {
                "ok": True,
                "identity": "user",
                "data": {
                    "spreadsheet_token": "spreadsheet-1",
                    "url": "https://example.feishu.cn/sheets/spreadsheet-1",
                },
            },
            {
                "ok": True,
                "identity": "user",
                "data": {
                    "sheets": [
                        {"sheet_id": "embedded", "resource_type": "bitable"},
                        {"sheet_id": "sheet-1", "resource_type": "sheet"},
                    ]
                },
            },
        ]
    )
    client = LarkCliFeishuClient(runner)

    spreadsheet = await client.create_spreadsheet(
        "巡检报告",
        "folder-1",
        [["巡检项"]],
        {"styles": [{"name": "Sheet1", "row_sizes": [{"range": "1:1", "type": "auto"}]}]},
    )

    assert spreadsheet.sheet_id == "sheet-1"
    assert runner.calls[1]["args"] == [
        "sheets",
        "+workbook-info",
        "--spreadsheet-token",
        "spreadsheet-1",
        "--as",
        "user",
        "--format",
        "json",
    ]


@pytest.mark.asyncio
async def test_report_client_uploads_cell_image_from_relative_cwd() -> None:
    runner = QueueRunner(
        [{"ok": True, "identity": "user", "data": {"updated_cells_count": 1}}]
    )
    client = LarkCliFeishuClient(runner)
    path = Path("/tmp/report images/001.jpg")

    await client.set_cell_image(
        "spreadsheet-1",
        "sheet-1",
        "C2",
        path,
        "原图.jpg",
    )

    call = runner.calls[0]
    assert call["args"] == [
        "sheets",
        "+cells-set-image",
        "--spreadsheet-token",
        "spreadsheet-1",
        "--sheet-id",
        "sheet-1",
        "--range",
        "C2",
        "--image",
        "001.jpg",
        "--name",
        "原图.jpg",
        "--as",
        "user",
        "--format",
        "json",
    ]
    assert call["stdin"] is None
    assert call["cwd"] == path.parent
    assert call["timeout"] == 90


@pytest.mark.asyncio
async def test_report_client_rejects_create_response_without_spreadsheet_reference() -> None:
    runner = QueueRunner(
        [{"ok": True, "identity": "user", "data": {"sheets": []}}]
    )
    client = LarkCliFeishuClient(runner)

    with pytest.raises(LarkCliCommandError) as raised:
        await client.create_spreadsheet(
            "巡检报告",
            "folder-1",
            [["巡检项"]],
            {"styles": [{"name": "Sheet1", "row_sizes": [{"range": "1:1", "type": "auto"}]}]},
        )

    assert raised.value.code == "lark_cli_invalid_response"


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
        await client.create_spreadsheet(
            "巡检报告",
            "folder-1",
            [["巡检项"]],
            {"styles": [{"name": "Sheet1", "row_sizes": [{"range": "1:1", "type": "auto"}]}]},
        )

    assert raised.value.code == "missing_scope"
