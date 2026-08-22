from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from app.api.errors import ApiError
from app.dependencies import get_feishu_oauth
from app.schemas import FeishuAuthorizationStart, FeishuAuthStatus
from app.services.feishu_oauth import (
    FeishuConfigurationError,
    FeishuOAuthProtocol,
    InvalidOAuthState,
)

router = APIRouter(prefix="/api/auth/feishu", tags=["feishu-auth"])


@router.get("/start", response_model=FeishuAuthorizationStart)
def start_authorization(
    oauth: Annotated[FeishuOAuthProtocol, Depends(get_feishu_oauth)],
) -> FeishuAuthorizationStart:
    try:
        return FeishuAuthorizationStart(authorization_url=oauth.authorization_url())
    except (FeishuConfigurationError, RuntimeError) as exc:
        raise ApiError(409, "feishu_not_configured", "请先配置飞书应用。") from exc


@router.get("/callback")
async def callback(
    code: str,
    state: str,
    oauth: Annotated[FeishuOAuthProtocol, Depends(get_feishu_oauth)],
) -> RedirectResponse:
    try:
        oauth.verify_state(state)
        await oauth.exchange_code(code)
    except (
        FeishuConfigurationError,
        InvalidOAuthState,
        ValidationError,
        httpx.HTTPError,
        RuntimeError,
    ):
        return RedirectResponse("/settings?feishu=error", status_code=303)
    return RedirectResponse("/settings?feishu=connected", status_code=303)


@router.get("/status", response_model=FeishuAuthStatus)
def authorization_status(
    oauth: Annotated[FeishuOAuthProtocol, Depends(get_feishu_oauth)],
) -> FeishuAuthStatus:
    try:
        configured, connected, expires_at = oauth.connection_status()
    except RuntimeError as exc:
        raise ApiError(
            503,
            "keychain_unavailable",
            "系统钥匙串不可用，请检查本机钥匙串服务。",
        ) from exc
    return FeishuAuthStatus(
        configured=configured,
        connected=connected,
        expires_at=expires_at,
    )


@router.delete("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(
    oauth: Annotated[FeishuOAuthProtocol, Depends(get_feishu_oauth)],
) -> Response:
    try:
        oauth.disconnect()
    except RuntimeError as exc:
        raise ApiError(
            503,
            "keychain_unavailable",
            "系统钥匙串不可用，请检查本机钥匙串服务。",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
