def template_payload(name: str) -> dict:
    return {
        "name": name,
        "description": "front desk visible checks",
        "task_prompt": "Inspect visible uniform details.",
        "rules": [
            {
                "rule_id": "badge",
                "name": "badge",
                "text": "Check whether a badge is visible on the left chest.",
            }
        ],
        "model_params": {"temperature": 0.1, "max_output_tokens": 2048},
    }


def test_seeded_templates_have_stable_slugs(app_harness) -> None:
    response = app_harness.client.get("/api/templates")

    assert response.status_code == 200
    assert {item["slug"] for item in response.json()} == {"general", "manager", "chef"}


def test_template_update_creates_version_and_preserves_version_one(app_harness) -> None:
    created = app_harness.client.post(
        "/api/templates",
        json=template_payload("front desk"),
    )
    assert created.status_code == 201
    assert created.json()["version"] == 1
    template_id = created.json()["id"]

    original = app_harness.client.get(f"/api/templates/{template_id}/versions/1").json()
    updated_payload = template_payload("front desk")
    updated_payload["rules"][0]["text"] = "The badge must be clearly visible."
    updated = app_harness.client.put(
        f"/api/templates/{template_id}",
        json=updated_payload,
    )

    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["rules"][0]["text"] == "The badge must be clearly visible."
    preserved = app_harness.client.get(f"/api/templates/{template_id}/versions/1")
    assert preserved.status_code == 200
    assert preserved.json() == original


def test_duplicate_template_name_returns_stable_conflict(app_harness) -> None:
    payload = template_payload("duplicate")
    assert app_harness.client.post("/api/templates", json=payload).status_code == 201

    response = app_harness.client.post("/api/templates", json=payload)

    assert response.status_code == 409
    assert response.json()["code"] == "template_name_exists"


def test_rule_id_validation_is_enforced(app_harness) -> None:
    payload = template_payload("invalid rule")
    payload["rules"][0]["rule_id"] = "Invalid ID"

    response = app_harness.client.post("/api/templates", json=payload)

    assert response.status_code == 422
