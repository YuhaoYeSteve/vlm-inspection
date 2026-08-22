from datetime import datetime
from typing import Any, Literal

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


class TemplateRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)


class TemplateModelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = Field(default=0.1, ge=0, le=2)
    max_output_tokens: int = Field(default=4096, ge=256, le=32768)


class TemplateWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    task_prompt: str = Field(default="", max_length=10000)
    rules: list[TemplateRule] = Field(min_length=1, max_length=100)
    model_params: TemplateModelParams = Field(default_factory=TemplateModelParams)


class TemplateSummary(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    enabled: bool
    version: int
    created_at: datetime


class TemplateDetail(TemplateSummary):
    version_id: str
    system_prompt: str
    task_prompt: str
    rules: list[TemplateRule]
    model_params: dict[str, Any]
