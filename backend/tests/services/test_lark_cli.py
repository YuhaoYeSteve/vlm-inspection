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
