import json
import time

from app.domain.states import TaskStatus

COMPLETE_USAGE = {
    "availability": "complete",
    "input_tokens": 10,
    "cached_input_tokens": 4,
    "uncached_input_tokens": 6,
    "output_tokens": 2,
    "reasoning_tokens": 0,
    "total_tokens": 12,
    "known_call_count": 1,
    "unknown_call_count": 0,
}

NOT_STARTED_USAGE = {
    "availability": "not_started",
    "input_tokens": 0,
    "cached_input_tokens": 0,
    "uncached_input_tokens": 0,
    "output_tokens": 0,
    "reasoning_tokens": 0,
    "total_tokens": 0,
    "known_call_count": 0,
    "unknown_call_count": 0,
}


def configure_ark(app_harness) -> None:
    response = app_harness.client.put(
        "/api/settings/ark",
        json={
            "base_url": "https://ark.example/v3",
            "protocol": "responses",
            "model": "seed-test",
            "api_key": "task-test-key",
            "concurrency": 3,
            "timeout_seconds": 120,
            "max_retries": 0,
            "thinking": "disabled",
        },
    )
    assert response.status_code == 200


def create_task(app_harness, name: str = "store inspection") -> str:
    configure_ark(app_harness)
    response = app_harness.client.post(
        "/api/tasks",
        json={"name": name, "template_id": app_harness.seeded_template_id},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def wait_for_status(app_harness, task_id: str, expected: set[str]) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = app_harness.client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        detail = response.json()
        if detail["status"] in expected:
            return detail
        time.sleep(0.01)
    raise AssertionError(f"task did not reach {expected}")


def test_new_task_reports_not_started_token_usage(app_harness) -> None:
    configure_ark(app_harness)

    response = app_harness.client.post(
        "/api/tasks",
        json={"name": "new task", "template_id": app_harness.seeded_template_id},
    )

    assert response.status_code == 201
    detail = response.json()
    assert detail["token_usage"] == NOT_STARTED_USAGE
    assert detail["images"] == []
    listed = app_harness.client.get("/api/tasks").json()
    assert listed[0]["token_usage"] == NOT_STARTED_USAGE


def test_create_upload_start_and_fetch_results(app_harness, png_file) -> None:
    task_id = create_task(app_harness)
    uploaded = app_harness.client.post(
        f"/api/tasks/{task_id}/images",
        files=[("files", ("one.png", png_file, "image/png"))],
    )

    assert uploaded.status_code == 201
    assert len(uploaded.json()["accepted"]) == 1
    assert app_harness.client.post(f"/api/tasks/{task_id}/start").status_code == 202

    detail = wait_for_status(app_harness, task_id, {TaskStatus.COMPLETED})
    assert detail["counts"]["pass"] == 1
    assert detail["counts"]["failed"] == 0
    assert detail["images"][0]["result"]["verdict"] == "pass"
    assert detail["token_usage"] == COMPLETE_USAGE
    assert detail["images"][0]["token_usage"] == COMPLETE_USAGE
    assert detail["images"][0]["token_calls"][0] == {
        "attempt_number": 1,
        "sequence": 1,
        "protocol": "responses",
        "purpose": "inspection",
        "status": "succeeded",
        "usage_error": None,
        "input_tokens": 10,
        "cached_input_tokens": 4,
        "output_tokens": 2,
        "reasoning_tokens": 0,
        "total_tokens": 12,
    }
    listed = app_harness.client.get("/api/tasks").json()
    task_summary = next(item for item in listed if item["id"] == task_id)
    assert task_summary["token_usage"] == COMPLETE_USAGE
    assert '"raw_usage"' not in json.dumps(detail)
    assert '"raw_usage"' not in json.dumps(task_summary)
    content = app_harness.client.get(
        f"/api/tasks/{task_id}/images/{detail['images'][0]['id']}/content",
        params={"variant": "thumbnail"},
    )
    assert content.status_code == 200
    assert content.headers["content-type"] == "image/jpeg"


def test_upload_skips_duplicate_hashes(app_harness, png_file) -> None:
    task_id = create_task(app_harness)

    response = app_harness.client.post(
        f"/api/tasks/{task_id}/images",
        files=[
            ("files", ("one.png", png_file, "image/png")),
            ("files", ("copy.png", png_file, "image/png")),
        ],
    )

    assert response.status_code == 201
    assert [item["filename"] for item in response.json()["accepted"]] == ["one.png"]
    assert [item["filename"] for item in response.json()["skipped"]] == ["copy.png"]


def test_rejects_101_files_before_ingestion(app_harness, png_file) -> None:
    task_id = create_task(app_harness)
    files = [
        ("files", (f"{index}.png", png_file + bytes([index % 256]), "image/png"))
        for index in range(101)
    ]

    response = app_harness.client.post(f"/api/tasks/{task_id}/images", files=files)

    assert response.status_code == 422
    assert response.json()["code"] == "task_image_limit"
    assert app_harness.client.get(f"/api/tasks/{task_id}").json()["images"] == []


def test_failed_image_can_retry_after_configuration_is_fixed(app_harness, png_file) -> None:
    task_id = create_task(app_harness)
    uploaded = app_harness.client.post(
        f"/api/tasks/{task_id}/images",
        files=[("files", ("one.png", png_file, "image/png"))],
    ).json()
    image_id = uploaded["accepted"][0]["id"]
    app_harness.inspector.error_status = 400
    assert app_harness.client.post(f"/api/tasks/{task_id}/start").status_code == 202
    paused = wait_for_status(app_harness, task_id, {TaskStatus.PAUSED})
    assert paused["images"][0]["status"] == "failed"

    app_harness.inspector.error_status = None
    retry = app_harness.client.post(f"/api/tasks/{task_id}/images/{image_id}/retry")

    assert retry.status_code == 202
    completed = wait_for_status(app_harness, task_id, {TaskStatus.COMPLETED})
    assert completed["counts"]["pass"] == 1
    partial_usage = {
        **COMPLETE_USAGE,
        "availability": "partial",
        "unknown_call_count": 1,
    }
    assert completed["token_usage"] == partial_usage
    assert completed["images"][0]["token_usage"] == partial_usage
    assert [call["attempt_number"] for call in completed["images"][0]["token_calls"]] == [
        1,
        2,
    ]
    assert completed["images"][0]["token_calls"][0] == {
        "attempt_number": 1,
        "sequence": 1,
        "protocol": "responses",
        "purpose": "inspection",
        "status": "failed",
        "usage_error": "not_returned",
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
    }
    assert completed["images"][0]["token_calls"][1]["total_tokens"] == 12
    assert '"raw_usage"' not in json.dumps(completed)


def test_cancel_prevents_more_uploads(app_harness, png_file) -> None:
    task_id = create_task(app_harness)

    assert app_harness.client.post(f"/api/tasks/{task_id}/cancel").status_code == 202
    response = app_harness.client.post(
        f"/api/tasks/{task_id}/images",
        files=[("files", ("one.png", png_file, "image/png"))],
    )

    assert response.status_code == 409
    assert response.json()["code"] == "task_not_draft"
