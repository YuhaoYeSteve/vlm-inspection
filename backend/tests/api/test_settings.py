from dataclasses import dataclass

from starlette.testclient import TestClient

from app.core.db import Base, make_engine
from app.main import create_app


def test_save_ark_settings_masks_secret(app_harness) -> None:
    client: TestClient = app_harness.client
    response = client.put(
        "/api/settings/ark",
        json={
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "protocol": "responses",
            "model": "doubao-seed-test",
            "api_key": "ark-secret-value",
            "concurrency": 3,
            "timeout_seconds": 120,
            "max_retries": 2,
            "thinking": "disabled",
        },
    )

    assert response.status_code == 200
    assert response.json()["api_key_configured"] is True
    assert "api_key" not in response.json()
    assert "ark-secret-value" not in response.text
    assert "ark-secret-value" not in app_harness.database_path.read_bytes().decode(
        "utf-8", errors="ignore"
    )
    assert app_harness.secrets.get("ark_api_key") == "ark-secret-value"

    saved = client.get("/api/settings/ark")
    assert saved.status_code == 200
    assert saved.json()["model"] == "doubao-seed-test"
    assert "ark-secret-value" not in saved.text


@dataclass
class UnavailableSecretStore:
    def get(self, key: str) -> str | None:
        raise RuntimeError("system keychain is unavailable")

    def set(self, key: str, value: str) -> None:
        raise RuntimeError("system keychain is unavailable")

    def delete(self, key: str) -> None:
        raise RuntimeError("system keychain is unavailable")


def test_keychain_failure_returns_stable_503(tmp_path) -> None:
    database_path = tmp_path / "unavailable.db"
    engine = make_engine(database_path)
    Base.metadata.create_all(engine)
    client = TestClient(create_app(engine=engine, secret_store=UnavailableSecretStore()))

    response = client.put(
        "/api/settings/ark",
        json={
            "base_url": "https://ark.example/v3",
            "protocol": "responses",
            "model": "seed",
            "api_key": "must-not-persist",
            "concurrency": 3,
            "timeout_seconds": 120,
            "max_retries": 2,
            "thinking": "disabled",
        },
    )

    assert response.status_code == 503
    assert response.json()["code"] == "keychain_unavailable"
    assert "must-not-persist" not in database_path.read_bytes().decode("utf-8", errors="ignore")


def test_accepts_maximum_concurrency(app_harness) -> None:
    response = app_harness.client.put(
        "/api/settings/ark",
        json={
            "base_url": "https://ark.example/v3",
            "protocol": "responses",
            "model": "seed",
            "concurrency": 500,
            "timeout_seconds": 120,
            "max_retries": 2,
            "thinking": "disabled",
        },
    )

    assert response.status_code == 200
    assert response.json()["concurrency"] == 500


def test_rejects_out_of_range_concurrency(app_harness) -> None:
    response = app_harness.client.put(
        "/api/settings/ark",
        json={
            "base_url": "https://ark.example/v3",
            "protocol": "responses",
            "model": "seed",
            "concurrency": 501,
            "timeout_seconds": 120,
            "max_retries": 2,
            "thinking": "disabled",
        },
    )

    assert response.status_code == 422


def test_ark_connection_uses_real_vision_adapter_path(app_harness) -> None:
    saved = app_harness.client.put(
        "/api/settings/ark",
        json={
            "base_url": "https://ark.example/v3",
            "protocol": "responses",
            "model": "seed-vision-endpoint",
            "api_key": "connection-test-secret",
            "concurrency": 3,
            "timeout_seconds": 120,
            "max_retries": 2,
            "thinking": "disabled",
        },
    )
    assert saved.status_code == 200

    response = app_harness.client.post("/api/settings/ark/test")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["protocol"] == "responses"
    assert response.json()["model"] == "seed-vision-endpoint"
    assert response.json()["latency_ms"] >= 0
    assert app_harness.inspector.call_count == 1
    assert "connection-test-secret" not in response.text


def test_feishu_credentials_cannot_be_written_through_settings(app_harness) -> None:
    response = app_harness.client.put(
        "/api/settings/feishu",
        json={"app_id": "cli", "app_secret": "secret", "folder_token": "folder"},
    )

    assert response.status_code == 404
