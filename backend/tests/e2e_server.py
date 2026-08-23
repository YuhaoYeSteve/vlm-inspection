import tempfile
from pathlib import Path

from app.core.config import Settings
from app.core.db import Base, make_engine, make_session_factory
from app.domain.results import Finding, FindingStatus, InspectionResult, Verdict
from app.main import create_app
from app.repositories import SettingsRepository
from app.services.ark import ArkRuntimeConfig, InspectionOutcome
from app.services.feishu_client import SpreadsheetRef
from tests.usage_fixtures import successful_usage


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class DeterministicInspectionService:
    def inspect(
        self,
        config: ArkRuntimeConfig,
        system_prompt: str,
        user_prompt: str,
        image_path: Path,
    ) -> InspectionOutcome:
        del system_prompt, user_prompt
        return InspectionOutcome(
            result=InspectionResult(
                image_valid=True,
                verdict=Verdict.PASS,
                summary=f"{image_path.name} 可见项目通过",
                findings=[
                    Finding(
                        rule_id="appearance",
                        rule_name="仪容仪表",
                        status=FindingStatus.PASS,
                        observation="可见区域无明显违规",
                        visual_evidence="人物上半身清晰可见",
                    )
                ],
            ),
            usage=successful_usage(config.protocol),
        )


class DeterministicFeishuClient:
    async def create_spreadsheet(
        self,
        title: str,
        folder_token: str,
        values: list[list[str]],
        styles: dict,
    ) -> SpreadsheetRef:
        del title, folder_token, values, styles
        return SpreadsheetRef(
            "report-1",
            "https://example.feishu.cn/sheets/report-1",
            "worksheet-1",
        )

    async def set_cell_image(
        self,
        spreadsheet_token: str,
        sheet_id: str,
        cell: str,
        image_path: Path,
        filename: str,
    ) -> None:
        del spreadsheet_token, sheet_id, cell, image_path, filename


_runtime = tempfile.TemporaryDirectory(prefix="vlm-inspector-e2e-")
_settings = Settings(data_dir=Path(_runtime.name))
_settings.ensure_directories()
_engine = make_engine(_settings.database_path)
Base.metadata.create_all(_engine)
_sessions = make_session_factory(_engine)
_secrets = MemorySecretStore()
_secrets.set("feishu_app_secret", "e2e-feishu-secret")
_secrets.set("feishu_access_token", "e2e-access-token")
_secrets.set("feishu_refresh_token", "e2e-refresh-token")
_secrets.set("feishu_access_token_expires_at", "4102444800")
_secrets.set("feishu_refresh_token_expires_at", "4102444800")
with _sessions() as session:
    SettingsRepository(session).set(
        "feishu",
        {"app_id": "cli_e2e", "folder_token": "folder-e2e"},
    )
    session.commit()

app = create_app(
    settings=_settings,
    engine=_engine,
    secret_store=_secrets,
    inspection_service=DeterministicInspectionService(),
    feishu_client=DeterministicFeishuClient(),
)
