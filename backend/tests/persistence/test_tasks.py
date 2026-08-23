from sqlalchemy import func, select

from app.core.db import Base, make_engine, make_session_factory
from app.domain.states import ImageStatus, ReportStatus, TaskStatus
from app.domain.usage import AttemptUsageCollector
from app.models import InferenceAttempt, InferenceResultRecord
from app.repositories import TaskRepository


def test_result_binds_to_image_id_and_recovers_running_items(tmp_path) -> None:
    engine = make_engine(tmp_path / "tasks.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)

    with sessions() as session:
        repo = TaskRepository(session)
        task = repo.create_task("night shift", {"template_version": 1}, {"model": "seed"})
        image = repo.add_image(task.id, 0, "a.png", "abc", "o.png", "i.jpg", "t.jpg")
        repo.set_task_status(task.id, TaskStatus.QUEUED)
        repo.set_task_status(task.id, TaskStatus.RUNNING)
        repo.set_image_status(image.id, ImageStatus.RUNNING)
        session.commit()

    with sessions() as session:
        repo = TaskRepository(session)
        recovered = repo.recover_interrupted_work()
        session.commit()

        assert recovered == 1
        assert repo.get_task(task.id).status == TaskStatus.PAUSED
        assert repo.get_image(image.id).status == ImageStatus.QUEUED


def test_claim_result_attempt_and_export_are_transactional(tmp_path) -> None:
    engine = make_engine(tmp_path / "queue.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)

    with sessions() as session:
        repo = TaskRepository(session)
        task = repo.create_task("morning shift", {"template_version": 2}, {"model": "seed"})
        second = repo.add_image(task.id, 1, "b.png", "bbb", "b.png", "b.jpg", "bt.jpg")
        first = repo.add_image(task.id, 0, "a.png", "aaa", "a.png", "a.jpg", "at.jpg")

        claimed = repo.claim_next_image(task.id)
        assert claimed is not None
        assert claimed.id == first.id
        assert claimed.status == ImageStatus.RUNNING
        assert claimed.attempt_count == 1

        usage = AttemptUsageCollector("responses")
        usage.add_response(
            "inspection",
            {
                "input_tokens": 10,
                "input_tokens_details": {"cached_tokens": 4},
                "output_tokens": 2,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 12,
            },
        )
        attempt = repo.record_attempt(
            first.id,
            status="succeeded",
            attempt_number=claimed.attempt_count,
            status_code=200,
            duration_ms=12,
            usage=usage.snapshot().model_dump(mode="json"),
        )
        old_result = repo.save_result(
            first.id,
            {"schema_version": 1, "verdict": "pass"},
            raw_text_redacted="first",
        )
        current_result = repo.save_result(
            first.id,
            {"schema_version": 1, "verdict": "fail"},
            raw_text_redacted="second",
            correction={"verdict": ["pass", "fail"]},
        )
        export = repo.get_or_create_active_export(task.id)
        same_export = repo.get_or_create_active_export(task.id)
        session.commit()

        assert old_result.is_current is False
        assert current_result.image_id == first.id
        assert current_result.is_current is True
        assert repo.get_image(second.id).status == ImageStatus.QUEUED
        assert export.id == same_export.id
        assert export.status == ReportStatus.PENDING
        assert attempt.usage_json is not None
        assert attempt.usage_json["schema_version"] == 1
        assert attempt.usage_json["totals"]["total_tokens"] == 12
        assert session.scalar(select(func.count()).select_from(InferenceAttempt)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(InferenceResultRecord)
                .where(InferenceResultRecord.is_current.is_(True))
            )
            == 1
        )


def test_reserved_retry_numbers_survive_interleaved_recording(tmp_path) -> None:
    engine = make_engine(tmp_path / "retry-reservations.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)

    with sessions() as session:
        repo = TaskRepository(session)
        task = repo.create_task("retry numbering", {"template_version": 1}, {"model": "seed"})
        image = repo.add_image(task.id, 0, "a.png", "aaa", "a.png", "a.jpg", "at.jpg")
        claimed = repo.claim_next_image(task.id)
        assert claimed is not None
        first_retry = repo.reserve_retry_attempt(image.id)
        second_retry = repo.reserve_retry_attempt(image.id)

        first_record = repo.record_attempt(
            image.id,
            status="failed",
            attempt_number=first_retry,
        )
        second_record = repo.record_attempt(
            image.id,
            status="succeeded",
            attempt_number=second_retry,
        )
        session.commit()

        assert [first_retry, second_retry] == [2, 3]
        assert [first_record.attempt_number, second_record.attempt_number] == [2, 3]
        assert repo.get_image(image.id).attempt_count == 3
