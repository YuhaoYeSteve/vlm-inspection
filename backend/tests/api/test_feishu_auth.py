def test_feishu_status_exposes_conversation_identity_and_destination(app_harness) -> None:
    response = app_harness.client.get("/api/auth/feishu/status")

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "connected": True,
        "user_name": "测试用户",
        "app_name": "示例应用",
        "identity": "user",
        "token_status": "valid",
        "expires_at": "2030-01-01T00:00:00Z",
        "destination_path": "测试报告目录",
        "reference_url": "https://example.feishu.cn/docx/reference",
        "error_code": None,
    }


def test_legacy_web_oauth_routes_are_not_exposed(app_harness) -> None:
    assert app_harness.client.get("/api/auth/feishu/start").status_code == 404
    assert app_harness.client.delete("/api/auth/feishu/disconnect").status_code == 405
