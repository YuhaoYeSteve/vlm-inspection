from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr


class ArkSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class FeishuSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(min_length=1)
    app_secret: SecretStr | None = None
    folder_token: str = ""


class FeishuSettingsRead(BaseModel):
    app_id: str
    folder_token: str
    callback_url: str
    app_secret_configured: bool
