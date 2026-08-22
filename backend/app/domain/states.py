from enum import StrEnum


class TaskStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    REVIEW_REQUIRED = "review_required"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    CANCELED = "canceled"


class ImageStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class ReportStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class InvalidTransition(ValueError):
    pass


_TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.DRAFT: {TaskStatus.QUEUED, TaskStatus.CANCELED},
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.CANCELED},
    TaskStatus.RUNNING: {
        TaskStatus.PAUSED,
        TaskStatus.REVIEW_REQUIRED,
        TaskStatus.EXPORTING,
        TaskStatus.CANCELED,
    },
    TaskStatus.PAUSED: {TaskStatus.QUEUED, TaskStatus.CANCELED},
    TaskStatus.REVIEW_REQUIRED: {
        TaskStatus.QUEUED,
        TaskStatus.EXPORTING,
        TaskStatus.CANCELED,
    },
    TaskStatus.EXPORTING: {TaskStatus.COMPLETED, TaskStatus.REVIEW_REQUIRED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.CANCELED: set(),
}


def require_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in _TASK_TRANSITIONS[current]:
        raise InvalidTransition(f"illegal task transition: {current} -> {target}")
