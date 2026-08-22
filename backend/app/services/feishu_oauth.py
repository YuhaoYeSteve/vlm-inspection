import base64
import hashlib
import hmac
import secrets as secrets_module
import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.secrets import SecretStoreProtocol
from app.models import AppSetting
from app.repositories import SettingsRepository

DEFAULT_FEISHU_REDIRECT_URI = "http://127.0.0.1:8000/api/auth/feishu/callback"


class InvalidOAuthState(ValueError):
    pass


class FeishuConfigurationError(RuntimeError):
    pass


class FeishuReauthorizationRequired(RuntimeError):
    pass


class AsyncHttpClientProtocol(Protocol):
    async def post(self, url: str, **kwargs: Any) -> Any: ...


class FeishuOAuthProtocol(Protocol):
    def authorization_url(self) -> str: ...

    def verify_state(self, encoded: str, now: int | None = None) -> None: ...

    async def exchange_code(self, code: str) -> None: ...

    async def get_valid_access_token(self) -> str: ...

    def connection_status(self) -> tuple[bool, bool, int | None]: ...

    def disconnect(self) -> None: ...


class OAuthTokenResponse(BaseModel):
    access_token: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    expires_in: int = Field(gt=0)
    refresh_token_expires_in: int = Field(gt=0)


class OAuthStateStore:
    KEY = "feishu_pending_oauth_state"

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def save(self, digest: str, expires_at: int) -> None:
        with self.sessions() as session:
            SettingsRepository(session).set(
                self.KEY,
                {"digest": digest, "expires_at": expires_at},
            )
            session.commit()

    def consume(self, digest: str, now: int) -> bool:
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            setting = session.get(AppSetting, self.KEY)
            if setting is None:
                session.commit()
                return False
            value = setting.value_json
            valid = value.get("digest") == digest and int(value.get("expires_at", 0)) >= now
            if valid or int(value.get("expires_at", 0)) < now:
                session.delete(setting)
            session.commit()
            return valid


class FeishuOAuthService:
    AUTHORIZATION_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    TOKEN_URL = "https://accounts.feishu.cn/oauth/v3/token"
    SCOPES = ("offline_access", "docx:document", "drive:drive")

    def __init__(
        self,
        sessions: sessionmaker[Session],
        secrets: SecretStoreProtocol,
        http: AsyncHttpClientProtocol | None = None,
        redirect_uri: str = DEFAULT_FEISHU_REDIRECT_URI,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.sessions = sessions
        self.secrets = secrets
        self.http = http
        self.redirect_uri = redirect_uri
        self.clock = clock
        self.state_store = OAuthStateStore(sessions)

    def make_state(self, now: int | None = None) -> str:
        timestamp = int(self.clock()) if now is None else now
        nonce = secrets_module.token_urlsafe(24)
        payload = f"{timestamp}.{nonce}"
        signature = hmac.new(
            self._app_secret().encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        encoded = base64.urlsafe_b64encode(f"{payload}.{signature}".encode()).decode()
        self.state_store.save(hashlib.sha256(encoded.encode()).hexdigest(), timestamp + 600)
        return encoded

    def verify_state(self, encoded: str, now: int | None = None) -> None:
        current = int(self.clock()) if now is None else now
        try:
            timestamp_text, nonce, signature = (
                base64.urlsafe_b64decode(encoded.encode()).decode().split(".")
            )
            timestamp = int(timestamp_text)
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidOAuthState("OAuth state is malformed") from exc
        payload = f"{timestamp_text}.{nonce}"
        expected = hmac.new(
            self._app_secret().encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        state_digest = hashlib.sha256(encoded.encode()).hexdigest()
        if (
            not hmac.compare_digest(signature, expected)
            or current - timestamp > 600
            or timestamp - current > 60
            or not self.state_store.consume(state_digest, current)
        ):
            raise InvalidOAuthState("OAuth state is invalid, expired, or already used")

    def authorization_url(self) -> str:
        verifier = secrets_module.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self.secrets.set("feishu_oauth_code_verifier", verifier)
        params = {
            "client_id": self._app_id(),
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            # offline_access is required for refresh tokens; Docx and Drive scopes
            # are required for document blocks, target folders, and image uploads.
            "scope": " ".join(self.SCOPES),
            "state": self.make_state(),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.AUTHORIZATION_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> None:
        verifier = self.secrets.get("feishu_oauth_code_verifier")
        if verifier is None:
            raise InvalidOAuthState("PKCE verifier is missing")
        try:
            response = await self._post(
                self.TOKEN_URL,
                json={
                    "grant_type": "authorization_code",
                    "client_id": self._app_id(),
                    "client_secret": self._app_secret(),
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "code_verifier": verifier,
                },
            )
            response.raise_for_status()
            token = OAuthTokenResponse.model_validate(response.json())
            self._save_token_response(token)
        finally:
            self.secrets.delete("feishu_oauth_code_verifier")

    async def get_valid_access_token(self) -> str:
        access_token = self.secrets.get("feishu_access_token")
        expires_text = self.secrets.get("feishu_access_token_expires_at")
        now = int(self.clock())
        if access_token is not None and expires_text is not None and int(expires_text) > now + 120:
            return access_token

        refresh_token = self.secrets.get("feishu_refresh_token")
        if refresh_token is None:
            self._clear_tokens()
            raise FeishuReauthorizationRequired("Feishu authorization is required")
        try:
            response = await self._post(
                self.TOKEN_URL,
                json={
                    "grant_type": "refresh_token",
                    "client_id": self._app_id(),
                    "client_secret": self._app_secret(),
                    "refresh_token": refresh_token,
                },
            )
            response.raise_for_status()
            token = OAuthTokenResponse.model_validate(response.json())
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            self._clear_tokens()
            raise FeishuReauthorizationRequired("Feishu token refresh failed") from exc
        self._save_token_response(token)
        return token.access_token

    def connection_status(self) -> tuple[bool, bool, int | None]:
        configured = bool(self._app_id(required=False) and self.secrets.get("feishu_app_secret"))
        access_token = self.secrets.get("feishu_access_token")
        refresh_token = self.secrets.get("feishu_refresh_token")
        expires_text = self.secrets.get("feishu_access_token_expires_at")
        expires_at = int(expires_text) if expires_text is not None else None
        return configured, bool(access_token and refresh_token), expires_at

    def disconnect(self) -> None:
        self._clear_tokens()
        self.secrets.delete("feishu_oauth_code_verifier")

    def _app_id(self, required: bool = True) -> str:
        with self.sessions() as session:
            settings = SettingsRepository(session).get("feishu") or {}
        app_id = str(settings.get("app_id", ""))
        if required and not app_id:
            raise FeishuConfigurationError("Feishu App ID is not configured")
        return app_id

    def _app_secret(self) -> str:
        app_secret = self.secrets.get("feishu_app_secret")
        if app_secret is None:
            raise FeishuConfigurationError("Feishu App Secret is not configured")
        return app_secret

    async def _post(self, url: str, **kwargs: Any) -> Any:
        if self.http is not None:
            return await self.http.post(url, **kwargs)
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.post(url, **kwargs)

    def _save_token_response(self, token: OAuthTokenResponse) -> None:
        now = int(self.clock())
        self.secrets.set("feishu_access_token", token.access_token)
        self.secrets.set("feishu_refresh_token", token.refresh_token)
        self.secrets.set("feishu_access_token_expires_at", str(now + token.expires_in))
        self.secrets.set(
            "feishu_refresh_token_expires_at",
            str(now + token.refresh_token_expires_in),
        )

    def _clear_tokens(self) -> None:
        for key in (
            "feishu_access_token",
            "feishu_refresh_token",
            "feishu_access_token_expires_at",
            "feishu_refresh_token_expires_at",
        ):
            self.secrets.delete(key)
