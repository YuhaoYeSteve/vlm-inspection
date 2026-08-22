from urllib.parse import parse_qs, urlparse


def configure_feishu(app_harness) -> None:
    response = app_harness.client.put(
        "/api/settings/feishu",
        json={
            "app_id": "cli_example",
            "app_secret": "feishu-app-secret",
            "folder_token": "folder-1",
        },
    )
    assert response.status_code == 200


def test_start_callback_status_and_disconnect(app_harness) -> None:
    configure_feishu(app_harness)
    started = app_harness.client.get("/api/auth/feishu/start")

    assert started.status_code == 200
    authorization_url = started.json()["authorization_url"]
    query = parse_qs(urlparse(authorization_url).query)
    assert query["client_id"] == ["cli_example"]
    assert query["code_challenge_method"] == ["S256"]
    assert "offline_access" in query["scope"][0]

    callback = app_harness.client.get(
        "/api/auth/feishu/callback",
        params={"code": "authorization-code", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/settings?feishu=connected"
    assert "authorization-code" not in callback.headers["location"]

    status = app_harness.client.get("/api/auth/feishu/status")
    assert status.status_code == 200
    assert status.json()["configured"] is True
    assert status.json()["connected"] is True
    assert status.json()["expires_at"] is not None

    disconnected = app_harness.client.delete("/api/auth/feishu/disconnect")
    assert disconnected.status_code == 204
    assert app_harness.secrets.get("feishu_access_token") is None
    assert app_harness.secrets.get("feishu_refresh_token") is None


def test_callback_error_redirects_without_leaking_code(app_harness) -> None:
    configure_feishu(app_harness)

    response = app_harness.client.get(
        "/api/auth/feishu/callback",
        params={"code": "sensitive-code", "state": "tampered"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?feishu=error"
    assert "sensitive-code" not in response.headers["location"]
