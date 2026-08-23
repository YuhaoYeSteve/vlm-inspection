import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.secrets import SecretStoreProtocol
from app.dependencies import ArkDependencies, get_ark_dependencies, get_secret_store, get_session
from app.repositories import SettingsRepository
from app.schemas import (
    ArkSettingsRead,
    ArkSettingsWrite,
    ArkTestResult,
)
from app.services.ark import ArkRequestError, MalformedInspectionResult
from app.services.prompts import SYSTEM_EVIDENCE_POLICY

router = APIRouter(prefix="/api/settings", tags=["settings"])

ARK_DEFAULTS: dict[str, Any] = {
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "protocol": "responses",
    "model": "",
    "concurrency": 3,
    "timeout_seconds": 120,
    "max_retries": 2,
    "thinking": "disabled",
}
def _secret_is_configured(secrets: SecretStoreProtocol, key: str) -> bool:
    try:
        return secrets.get(key) is not None
    except RuntimeError as exc:
        raise ApiError(
            503,
            "keychain_unavailable",
            "系统钥匙串不可用，请检查本机钥匙串服务。",
        ) from exc


def _save_secret(secrets: SecretStoreProtocol, key: str, value: str) -> None:
    try:
        secrets.set(key, value)
    except RuntimeError as exc:
        raise ApiError(
            503,
            "keychain_unavailable",
            "系统钥匙串不可用，请检查本机钥匙串服务。",
        ) from exc


def _ark_response(public: dict[str, Any], secrets: SecretStoreProtocol) -> ArkSettingsRead:
    return ArkSettingsRead(
        **public,
        api_key_configured=_secret_is_configured(secrets, "ark_api_key"),
    )


@router.get("/ark", response_model=ArkSettingsRead)
def get_ark_settings(
    session: Annotated[Session, Depends(get_session)],
    secrets: Annotated[SecretStoreProtocol, Depends(get_secret_store)],
) -> ArkSettingsRead:
    public = SettingsRepository(session).get("ark") or ARK_DEFAULTS
    return _ark_response(public, secrets)


@router.put("/ark", response_model=ArkSettingsRead)
def save_ark_settings(
    body: ArkSettingsWrite,
    session: Annotated[Session, Depends(get_session)],
    secrets: Annotated[SecretStoreProtocol, Depends(get_secret_store)],
) -> ArkSettingsRead:
    if body.api_key is not None:
        _save_secret(secrets, "ark_api_key", body.api_key.get_secret_value())
    public = body.model_dump(exclude={"api_key"}, mode="json")
    SettingsRepository(session).set("ark", public)
    session.commit()
    return _ark_response(public, secrets)


@router.post("/ark/test", response_model=ArkTestResult)
def test_ark_connection(
    deps: Annotated[ArkDependencies, Depends(get_ark_dependencies)],
) -> ArkTestResult:
    started = time.monotonic()
    try:
        deps.inspector.inspect(
            deps.runtime_config,
            SYSTEM_EVIDENCE_POLICY,
            "检查图片是否可读取，并按固定结构返回。",
            deps.bundled_test_image,
        )
    except (ArkRequestError, MalformedInspectionResult) as exc:
        raise ApiError(502, "ark_connection_failed", "方舟视觉模型连接测试失败。") from exc
    return ArkTestResult(
        ok=True,
        protocol=deps.runtime_config.protocol,
        model=deps.runtime_config.model,
        latency_ms=round((time.monotonic() - started) * 1000),
    )
