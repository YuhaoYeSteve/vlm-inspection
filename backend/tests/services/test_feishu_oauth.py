from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from app.core.db import Base, make_engine, make_session_factory
from app.repositories import SettingsRepository
from app.services.feishu_oauth import (
    FeishuOAuthService,
    FeishuReauthorizationRequired,
    InvalidOAuthState,
)


@dataclass
class MemorySecrets:
    values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class FakeHttp:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.posts: list[dict] = []

    async def post(self, url: str, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return self.responses.pop(0)


def token_response(
    access_token: str = "access-1",
    refresh_token: str = "refresh-1",
    status: int = 200,
) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": 7200,
            "refresh_token_expires_in": 2_592_000,
        },
        request=httpx.Request("POST", "https://accounts.feishu.cn/oauth/v3/token"),
    )


@dataclass
class OAuthHarness:
    service: FeishuOAuthService
    secrets: MemorySecrets
    database_path: Path
    http: FakeHttp


@pytest.fixture
def oauth_harness(tmp_path: Path) -> OAuthHarness:
    database_path = tmp_path / "oauth.db"
    engine = make_engine(database_path)
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions() as session:
        SettingsRepository(session).set(
            "feishu",
            {"app_id": "cli_example", "folder_token": "folder-1"},
        )
        session.commit()
    secrets = MemorySecrets({"feishu_app_secret": "app-secret"})
    http = FakeHttp([token_response()])
    service = FeishuOAuthService(sessions, secrets, http=http, clock=lambda: 1_787_398_400)
    return OAuthHarness(service, secrets, database_path, http)


def test_oauth_state_rejects_tampering_and_replay(oauth_harness) -> None:
    state = oauth_harness.service.make_state(now=1_787_398_400)

    oauth_harness.service.verify_state(state, now=1_787_398_500)
    with pytest.raises(InvalidOAuthState):
        oauth_harness.service.verify_state(state, now=1_787_398_500)
    with pytest.raises(InvalidOAuthState):
        oauth_harness.service.verify_state(state + "x", now=1_787_398_500)


@pytest.mark.asyncio
async def test_exchange_saves_tokens_only_in_secret_store(oauth_harness) -> None:
    oauth_harness.service.authorization_url()

    await oauth_harness.service.exchange_code("code-1")

    assert oauth_harness.secrets.get("feishu_access_token") == "access-1"
    assert oauth_harness.secrets.get("feishu_refresh_token") == "refresh-1"
    assert oauth_harness.secrets.get("feishu_oauth_code_verifier") is None
    assert "access-1" not in oauth_harness.database_path.read_bytes().decode(
        "utf-8", errors="ignore"
    )
    body = oauth_harness.http.posts[0]["json"]
    assert body["grant_type"] == "authorization_code"
    assert body["code_verifier"]


@pytest.mark.asyncio
async def test_expiring_access_token_is_refreshed(oauth_harness) -> None:
    oauth_harness.secrets.values.update(
        {
            "feishu_access_token": "old-access",
            "feishu_refresh_token": "old-refresh",
            "feishu_access_token_expires_at": "1787398450",
        }
    )

    token = await oauth_harness.service.get_valid_access_token()

    assert token == "access-1"
    assert oauth_harness.http.posts[0]["json"]["grant_type"] == "refresh_token"


@pytest.mark.asyncio
async def test_failed_refresh_clears_stale_tokens(oauth_harness) -> None:
    oauth_harness.http.responses[:] = [token_response(status=400)]
    oauth_harness.secrets.values.update(
        {
            "feishu_access_token": "old-access",
            "feishu_refresh_token": "old-refresh",
            "feishu_access_token_expires_at": "1787398450",
        }
    )

    with pytest.raises(FeishuReauthorizationRequired):
        await oauth_harness.service.get_valid_access_token()

    assert oauth_harness.secrets.get("feishu_access_token") is None
    assert oauth_harness.secrets.get("feishu_refresh_token") is None
