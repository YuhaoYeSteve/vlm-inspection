from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.secrets import SecretStoreProtocol
from app.dependencies import get_secret_store, get_session
from app.repositories import SettingsRepository
from app.schemas import (
    ArkSettingsRead,
    ArkSettingsWrite,
    FeishuSettingsRead,
    FeishuSettingsWrite,
)

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
FEISHU_CALLBACK_URL = "http://127.0.0.1:8000/api/auth/feishu/callback"


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


def _feishu_response(
    public: dict[str, Any],
    secrets: SecretStoreProtocol,
) -> FeishuSettingsRead:
    return FeishuSettingsRead(
        **public,
        callback_url=FEISHU_CALLBACK_URL,
        app_secret_configured=_secret_is_configured(secrets, "feishu_app_secret"),
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


@router.get("/feishu", response_model=FeishuSettingsRead)
def get_feishu_settings(
    session: Annotated[Session, Depends(get_session)],
    secrets: Annotated[SecretStoreProtocol, Depends(get_secret_store)],
) -> FeishuSettingsRead:
    public = SettingsRepository(session).get("feishu") or {"app_id": "", "folder_token": ""}
    return _feishu_response(public, secrets)


@router.put("/feishu", response_model=FeishuSettingsRead)
def save_feishu_settings(
    body: FeishuSettingsWrite,
    session: Annotated[Session, Depends(get_session)],
    secrets: Annotated[SecretStoreProtocol, Depends(get_secret_store)],
) -> FeishuSettingsRead:
    if body.app_secret is not None:
        _save_secret(secrets, "feishu_app_secret", body.app_secret.get_secret_value())
    public = body.model_dump(exclude={"app_secret"}, mode="json")
    SettingsRepository(session).set("feishu", public)
    session.commit()
    return _feishu_response(public, secrets)
