from pathlib import Path


def read_tree(root: Path) -> bytes:
    if not root.exists():
        return b""
    return b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())


def test_secrets_exist_only_in_secret_store(app_harness) -> None:
    ark_secret = "ARK_SENTINEL_SECRET"
    feishu_secret = "FEISHU_SENTINEL_SECRET"
    token_secret = "TOKEN_SENTINEL_SECRET"

    ark_response = app_harness.client.put(
        "/api/settings/ark",
        json={
            "base_url": "https://ark.example/v3",
            "protocol": "responses",
            "model": "seed-security-test",
            "api_key": ark_secret,
            "concurrency": 3,
            "timeout_seconds": 120,
            "max_retries": 2,
            "thinking": "disabled",
        },
    )
    feishu_response = app_harness.client.put(
        "/api/settings/feishu",
        json={
            "app_id": "cli_security_test",
            "app_secret": feishu_secret,
            "folder_token": "folder-public-value",
        },
    )
    app_harness.secrets.set("feishu_access_token", token_secret)
    app_harness.secrets.set("feishu_refresh_token", "refresh-public-test")
    app_harness.secrets.set("feishu_access_token_expires_at", "4102444800")
    status_response = app_harness.client.get("/api/auth/feishu/status")
    api_responses = b"".join(
        response.content
        for response in (
            ark_response,
            feishu_response,
            status_response,
            app_harness.client.get("/api/settings/ark"),
            app_harness.client.get("/api/settings/feishu"),
            app_harness.client.get("/api/tasks"),
        )
    )

    database_files = b"".join(
        path.read_bytes()
        for path in app_harness.database_path.parent.glob(f"{app_harness.database_path.name}*")
        if path.is_file()
    )
    logs = read_tree(app_harness.database_path.parent / "data" / "logs")
    inspected = database_files + logs + api_responses

    for sentinel in (ark_secret, feishu_secret, token_secret):
        assert sentinel.encode() not in inspected
        assert sentinel in app_harness.secrets.values.values()
