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


class ArkTestResult(BaseModel):
    ok: bool
    protocol: Literal["responses", "chat"]
    model: str
    latency_ms: int


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


class FeishuAuthorizationStart(BaseModel):
    authorization_url: str


class FeishuAuthStatus(BaseModel):
    configured: bool
    connected: bool
    expires_at: int | None


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


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    template_id: str = Field(min_length=1)


class TaskCounts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    passed: int = Field(default=0, alias="pass")
    fail: int = 0
    unverifiable: int = 0
    failed: int = 0


class TaskImageRead(BaseModel):
    id: str
    position: int
    original_name: str
    sha256: str
    status: str
    attempt_count: int
    error_code: str | None
    error_message: str | None
    original_url: str
    thumbnail_url: str
    result: dict[str, Any] | None


class TaskSummary(BaseModel):
    id: str
    name: str
    status: str
    template_name: str
    template_version: int
    model: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    counts: TaskCounts
    report_id: str | None = None
    report_status: str | None = None
    report_url: str | None = None


class TaskDetail(TaskSummary):
    template_snapshot: dict[str, Any]
    model_snapshot: dict[str, Any]
    images: list[TaskImageRead]


class UploadAccepted(BaseModel):
    id: str
    filename: str
    sha256: str


class UploadIssue(BaseModel):
    filename: str
    reason: str


class UploadSummary(BaseModel):
    accepted: list[UploadAccepted]
    skipped: list[UploadIssue]
    rejected: list[UploadIssue]


class TaskActionAccepted(BaseModel):
    task_id: str
    status: Literal["accepted"] = "accepted"


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_failures: bool = False


class ReportAccepted(BaseModel):
    export_id: str
    status: Literal["accepted"] = "accepted"
