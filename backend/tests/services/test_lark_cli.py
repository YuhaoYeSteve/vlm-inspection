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
            "docx:document:create docx:document:write_only "
            "drive:file:upload offline_access"
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
    assert any("folder-1" in argument for argument in call)


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
    assert any("docx_image" in argument for argument in call["args"])


@pytest.mark.asyncio
async def test_report_client_appends_docx_xml_through_stdin() -> None:
    runner = QueueRunner(
        [{"ok": True, "identity": "user", "data": {"result": "success"}}]
    )
    client = LarkCliFeishuClient(runner)

    await client.append_document_xml("doc-1", "<table></table>", "matrix:1")

    call = runner.calls[0]
    assert call["args"] == [
        "docs",
        "+update",
        "--doc",
        "doc-1",
        "--command",
        "append",
        "--doc-format",
        "xml",
        "--content",
        "-",
        "--as",
        "user",
        "--format",
        "json",
    ]
    assert call["stdin"] == b"<table></table>"


@pytest.mark.asyncio
async def test_report_client_rejects_unsuccessful_docx_xml_update() -> None:
    runner = QueueRunner(
        [{"ok": True, "identity": "user", "data": {"result": "failed"}}]
    )
    client = LarkCliFeishuClient(runner)

    with pytest.raises(LarkCliCommandError) as raised:
        await client.append_document_xml("doc-1", "<table></table>", "matrix:1")

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
        await client.create_document("巡检报告", "folder-1", "export-1")

    assert raised.value.code == "missing_scope"
