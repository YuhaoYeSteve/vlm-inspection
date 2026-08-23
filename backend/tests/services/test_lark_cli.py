import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.services.feishu_client import BlockRef, DocumentRef
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
                        "docx:document:create docx:document:readonly "
                        "docx:document:write_only drive:file:upload offline_access"
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
                    "scope": "docx:document:readonly drive:file:upload",
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
            "docx:document:create docx:document:readonly "
            "docx:document:write_only drive:file:upload offline_access"
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
async def test_report_client_creates_document_with_stable_client_token() -> None:
    runner = QueueRunner(
        [
            {
                "ok": True,
                "identity": "user",
                "data": {
                    "document": {
                        "document_id": "document-1",
                        "url": "https://example.feishu.cn/docx/document-1",
                    }
                },
            }
        ]
    )
    client = LarkCliFeishuClient(runner)

    document = await client.create_document("巡检报告", "folder-1", "export-1")

    assert document == DocumentRef(
        "document-1",
        "https://example.feishu.cn/docx/document-1",
    )
    call = runner.calls[0]
    assert call["args"][:3] == [
        "api",
        "POST",
        "/open-apis/docx/v1/documents",
    ]
    params = json.loads(call["args"][call["args"].index("--params") + 1])
    assert params == {
        "client_token": hashlib.sha256(b"export-1").hexdigest()[:32]
    }
    body = json.loads(call["args"][call["args"].index("--data") + 1])
    assert body == {"title": "巡检报告", "folder_token": "folder-1"}
    assert call["timeout"] == 60


@pytest.mark.asyncio
async def test_report_client_appends_and_fetches_document_xml() -> None:
    runner = QueueRunner(
        [
            {
                "ok": True,
                "identity": "user",
                "data": {
                    "result": "success",
                    "document": {"revision_id": 2},
                },
            },
            {
                "ok": True,
                "identity": "user",
                "data": {
                    "document": {
                        "document_id": "document-1",
                        "content": '<table id="table-1"></table>',
                    }
                },
            },
        ]
    )
    client = LarkCliFeishuClient(runner)

    await client.append_document_xml("document-1", "<table></table>", "doc:layout")
    content = await client.fetch_document_xml("document-1")

    assert content == '<table id="table-1"></table>'
    assert runner.calls[0]["args"] == [
        "docs",
        "+update",
        "--doc",
        "document-1",
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
    assert runner.calls[0]["stdin"] == b"<table></table>"
    assert runner.calls[1]["args"] == [
        "docs",
        "+fetch",
        "--doc",
        "document-1",
        "--detail",
        "with-ids",
        "--as",
        "user",
        "--format",
        "json",
    ]


@pytest.mark.asyncio
async def test_report_client_creates_uploads_binds_and_moves_image_to_table_cell() -> None:
    runner = QueueRunner(
        [
            {
                "ok": True,
                "identity": "user",
                "data": {"children": [{"block_id": "image-block-1"}]},
            },
            {
                "ok": True,
                "identity": "user",
                "data": {"file_token": "file-token-1"},
            },
            {
                "ok": True,
                "identity": "user",
                "data": {
                    "blocks": [
                        {
                            "block_id": "image-block-1",
                            "block_type": 27,
                            "image": {"token": "file-token-1"},
                        }
                    ],
                    "document_revision_id": 3,
                },
            },
            {
                "ok": True,
                "identity": "user",
                "data": {"result": "success", "document": {"revision_id": 4}},
            },
        ]
    )
    client = LarkCliFeishuClient(runner)

    block = await client.create_image_block(
        "document-1",
        "document-1",
        "doc-image-block:image-1",
    )
    token = await client.upload_docx_image(block.id, "原图.jpg", b"jpeg")
    await client.bind_docx_image("document-1", block.id, token)
    await client.move_document_block("document-1", "anchor-b", block.id)

    assert block == BlockRef("image-block-1")
    assert token == "file-token-1"
    create_call = runner.calls[0]
    assert create_call["args"][:3] == [
        "api",
        "POST",
        "/open-apis/docx/v1/documents/document-1/blocks/document-1/children",
    ]
    create_params = json.loads(
        create_call["args"][create_call["args"].index("--params") + 1]
    )
    assert create_params["document_revision_id"] == "-1"
    create_body = json.loads(
        create_call["args"][create_call["args"].index("--data") + 1]
    )
    assert create_body == {"children": [{"block_type": 27, "image": {}}], "index": -1}
    upload_call = runner.calls[1]
    assert upload_call["args"][:3] == [
        "api",
        "POST",
        "/open-apis/drive/v1/medias/upload_all",
    ]
    upload_body = json.loads(
        upload_call["args"][upload_call["args"].index("--data") + 1]
    )
    assert upload_body == {
        "file_name": "原图.jpg",
        "parent_type": "docx_image",
        "parent_node": "image-block-1",
        "size": "4",
    }
    assert upload_call["stdin"] == b"jpeg"
    assert upload_call["timeout"] == 90
    bind_call = runner.calls[2]
    assert bind_call["args"][:3] == [
        "api",
        "PATCH",
        "/open-apis/docx/v1/documents/document-1/blocks/batch_update",
    ]
    bind_params = json.loads(
        bind_call["args"][bind_call["args"].index("--params") + 1]
    )
    assert bind_params == {"document_revision_id": "-1"}
    bind_body = json.loads(
        bind_call["args"][bind_call["args"].index("--data") + 1]
    )
    assert bind_body == {
        "requests": [
            {
                "block_id": "image-block-1",
                "replace_image": {"token": "file-token-1"},
            }
        ]
    }
    assert runner.calls[3]["args"] == [
        "docs",
        "+update",
        "--doc",
        "document-1",
        "--command",
        "block_move_after",
        "--block-id",
        "anchor-b",
        "--src-block-ids",
        "image-block-1",
        "--as",
        "user",
        "--format",
        "json",
    ]


@pytest.mark.asyncio
async def test_report_client_rejects_create_response_without_document_reference() -> None:
    runner = QueueRunner(
        [{"ok": True, "identity": "user", "data": {"document": {}}}]
    )
    client = LarkCliFeishuClient(runner)

    with pytest.raises(LarkCliCommandError) as raised:
        await client.create_document("巡检报告", "folder-1", "export-1")

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
        await client.append_document_xml("document-1", "<table></table>", "doc:layout")

    assert raised.value.code == "missing_scope"
