from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import InspectionTask, TaskImage


def test_template_test_run_does_not_create_task(app_harness, png_file) -> None:
    configured = app_harness.client.put(
        "/api/settings/ark",
        json={
            "base_url": "https://ark.example/v3",
            "protocol": "responses",
            "model": "seed-test",
            "api_key": "template-test-key",
            "concurrency": 3,
            "timeout_seconds": 120,
            "max_retries": 2,
            "thinking": "disabled",
        },
    )
    assert configured.status_code == 200

    response = app_harness.client.post(
        f"/api/templates/{app_harness.seeded_template_id}/test",
        files={"file": ("sample.png", png_file, "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["verdict"] == "pass"
    assert app_harness.inspector.call_count == 1
    with Session(create_engine(f"sqlite:///{app_harness.database_path}")) as session:
        assert session.scalar(select(func.count()).select_from(InspectionTask)) == 0
        assert session.scalar(select(func.count()).select_from(TaskImage)) == 0
