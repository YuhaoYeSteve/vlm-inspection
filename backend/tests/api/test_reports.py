import time


def configure_services(app_harness) -> None:
    ark = app_harness.client.put(
        "/api/settings/ark",
        json={
            "base_url": "https://ark.example/v3",
            "protocol": "responses",
            "model": "seed-test",
            "api_key": "ark-key",
            "concurrency": 2,
            "timeout_seconds": 120,
            "max_retries": 0,
            "thinking": "disabled",
        },
    )
    assert ark.status_code == 200


def create_uploaded_task(app_harness, png_file) -> tuple[str, str]:
    configure_services(app_harness)
    template_id = app_harness.seeded_template_id
    created = app_harness.client.post(
        "/api/tasks",
        json={"name": "report api task", "template_id": template_id},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    uploaded = app_harness.client.post(
        f"/api/tasks/{task_id}/images",
        files=[("files", ("one.png", png_file, "image/png"))],
    )
    assert uploaded.status_code == 201
    return str(task_id), str(uploaded.json()["accepted"][0]["id"])


def wait_for_task(app_harness, task_id: str, statuses: set[str]) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        detail = app_harness.client.get(f"/api/tasks/{task_id}").json()
        if detail["status"] in statuses:
            return detail
        time.sleep(0.01)
    raise AssertionError(f"task did not reach {statuses}")


def test_all_success_task_automatically_creates_one_report(app_harness, png_file) -> None:
    task_id, _image_id = create_uploaded_task(app_harness, png_file)

    assert app_harness.client.post(f"/api/tasks/{task_id}/start").status_code == 202
    detail = wait_for_task(app_harness, task_id, {"completed"})

    assert detail["report_status"] == "succeeded"
    assert detail["report_url"] == "https://example.feishu.cn/docx/report-1"
    assert app_harness.feishu_client.created_documents == 1


def test_review_task_requires_confirmation_then_exports_without_model_retry(
    app_harness,
    png_file,
) -> None:
    task_id, _image_id = create_uploaded_task(app_harness, png_file)
    app_harness.inspector.malformed = True
    assert app_harness.client.post(f"/api/tasks/{task_id}/start").status_code == 202
    wait_for_task(app_harness, task_id, {"review_required"})
    model_calls = app_harness.inspector.call_count

    refused = app_harness.client.post(
        f"/api/tasks/{task_id}/report",
        json={"include_failures": False},
    )
    assert refused.status_code == 409

    accepted = app_harness.client.post(
        f"/api/tasks/{task_id}/report",
        json={"include_failures": True},
    )
    assert accepted.status_code == 202
    detail = wait_for_task(app_harness, task_id, {"completed"})

    assert detail["report_status"] == "succeeded"
    assert app_harness.inspector.call_count == model_calls


def test_new_report_version_creates_another_document_without_new_inference(
    app_harness,
    png_file,
) -> None:
    task_id, _image_id = create_uploaded_task(app_harness, png_file)
    assert app_harness.client.post(f"/api/tasks/{task_id}/start").status_code == 202
    wait_for_task(app_harness, task_id, {"completed"})
    model_calls = app_harness.inspector.call_count

    response = app_harness.client.post(f"/api/tasks/{task_id}/report/new-version")

    assert response.status_code == 202
    deadline = time.monotonic() + 3
    while (
        time.monotonic() < deadline
        and app_harness.feishu_client.created_documents < 2
    ):
        time.sleep(0.01)
    assert app_harness.feishu_client.created_documents == 2
    assert app_harness.inspector.call_count == model_calls
