import pytest

from app.domain.states import InvalidTransition, TaskStatus, require_task_transition


def test_running_can_pause() -> None:
    require_task_transition(TaskStatus.RUNNING, TaskStatus.PAUSED)


def test_completed_cannot_restart() -> None:
    with pytest.raises(InvalidTransition):
        require_task_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING)


def test_paused_can_return_to_queue() -> None:
    require_task_transition(TaskStatus.PAUSED, TaskStatus.QUEUED)
