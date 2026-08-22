import asyncio
import json
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Header, UploadFile, status
from fastapi.responses import FileResponse
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.api.errors import ApiError
from app.dependencies import (
    ArkDependencies,
    TaskDependencies,
    get_ark_dependencies,
    get_event_broker,
    get_task_dependencies,
)
from app.domain.states import InvalidTransition, TaskStatus
from app.models import InspectionTask
from app.repositories import SettingsRepository, TemplateRepository
from app.schemas import (
    TaskActionAccepted,
    TaskCounts,
    TaskCreate,
    TaskDetail,
    TaskImageRead,
    TaskSummary,
    UploadAccepted,
    UploadIssue,
    UploadSummary,
)
from app.services.events import TaskEventBroker
from app.services.images import DuplicateImage, UnsupportedImage
from app.services.runner import TaskAlreadyRunning

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _not_found(resource: str) -> ApiError:
    return ApiError(404, "task_resource_not_found", f"未找到任务资源：{resource}")


def _task_counts(deps: TaskDependencies, task_id: str) -> TaskCounts:
    counts = {"pass": 0, "fail": 0, "unverifiable": 0, "failed": 0}
    for image in deps.repo.list_images(task_id):
        if image.status == "failed":
            counts["failed"] += 1
        result = deps.repo.get_current_result(image.id)
        if result is not None:
            verdict = str(result.result_json.get("verdict", ""))
            if verdict in {"pass", "fail", "unverifiable"}:
                counts[verdict] += 1
    return TaskCounts.model_validate(counts)


def _task_summary(deps: TaskDependencies, task: InspectionTask) -> TaskSummary:
    template = task.template_snapshot_json
    model = task.model_snapshot_json
    return TaskSummary(
        id=task.id,
        name=task.name,
        status=task.status,
        template_name=str(template.get("template_name", "")),
        template_version=int(template.get("template_version", 0)),
        model=str(model.get("model", "")),
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        counts=_task_counts(deps, task.id),
    )


def _task_detail(deps: TaskDependencies, task: InspectionTask) -> TaskDetail:
    summary = _task_summary(deps, task)
    images = []
    for image in deps.repo.list_images(task.id):
        result = deps.repo.get_current_result(image.id)
        images.append(
            TaskImageRead(
                id=image.id,
                position=image.position,
                original_name=image.original_name,
                sha256=image.sha256,
                status=image.status,
                attempt_count=image.attempt_count,
                error_code=image.error_code,
                error_message=image.error_message,
                original_url=f"/api/tasks/{task.id}/images/{image.id}/content?variant=original",
                thumbnail_url=f"/api/tasks/{task.id}/images/{image.id}/content?variant=thumbnail",
                result=None if result is None else result.result_json,
            )
        )
    return TaskDetail(
        **summary.model_dump(),
        template_snapshot=task.template_snapshot_json,
        model_snapshot=task.model_snapshot_json,
        images=images,
    )


@router.post("", response_model=TaskDetail, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskCreate,
    deps: Annotated[TaskDependencies, Depends(get_task_dependencies)],
    ark: Annotated[ArkDependencies, Depends(get_ark_dependencies)],
) -> TaskDetail:
    del ark
    templates = TemplateRepository(deps.session)
    try:
        template = templates.get(body.template_id)
        version = templates.get_current_version(body.template_id)
    except LookupError as exc:
        raise ApiError(404, "template_not_found", "未找到提示词模板。") from exc
    ark_settings = SettingsRepository(deps.session).get("ark")
    if ark_settings is None:
        raise ApiError(409, "ark_not_configured", "请先配置火山方舟连接。")
    task = deps.repo.create_task(
        body.name,
        {
            "template_id": template.id,
            "template_name": template.name,
            "template_version_id": version.id,
            "template_version": version.version,
            "system_prompt": version.system_prompt,
            "task_prompt": version.task_prompt,
            "rules": version.rules_json,
            "model_params": version.model_params_json,
        },
        ark_settings,
    )
    deps.session.commit()
    return _task_detail(deps, task)


@router.get("", response_model=list[TaskSummary])
def list_tasks(
    deps: Annotated[TaskDependencies, Depends(get_task_dependencies)],
) -> list[TaskSummary]:
    return [_task_summary(deps, task) for task in deps.repo.list_tasks()]


@router.get("/{task_id}", response_model=TaskDetail)
def get_task(
    task_id: str,
    deps: Annotated[TaskDependencies, Depends(get_task_dependencies)],
) -> TaskDetail:
    try:
        task = deps.repo.get_task(task_id)
    except LookupError as exc:
        raise _not_found(task_id) from exc
    return _task_detail(deps, task)


@router.post(
    "/{task_id}/images",
    status_code=status.HTTP_201_CREATED,
    response_model=UploadSummary,
)
async def upload_images(
    task_id: str,
    files: Annotated[list[UploadFile], File()],
    deps: Annotated[TaskDependencies, Depends(get_task_dependencies)],
) -> UploadSummary:
    try:
        task = deps.repo.get_task(task_id)
    except LookupError as exc:
        raise _not_found(task_id) from exc
    if TaskStatus(task.status) is not TaskStatus.DRAFT:
        raise ApiError(409, "task_not_draft", "只能向草稿任务添加图片。")
    existing_count = deps.repo.count_images(task_id)
    if existing_count + len(files) > 100:
        raise ApiError(422, "task_image_limit", "单个任务最多 100 张图片。")

    accepted: list[UploadAccepted] = []
    skipped: list[UploadIssue] = []
    rejected: list[UploadIssue] = []
    position = existing_count
    for upload in files:
        filename = Path(upload.filename or "image").name or "image"
        if upload.content_type not in _ALLOWED_IMAGE_MIME_TYPES:
            rejected.append(UploadIssue(filename=filename, reason="unsupported_content_type"))
            continue
        content = await upload.read(deps.ingestor.MAX_BYTES + 1)
        try:
            ingested = deps.ingestor.ingest(task_id, filename, content)
        except DuplicateImage:
            skipped.append(UploadIssue(filename=filename, reason="duplicate_image"))
            continue
        except UnsupportedImage as exc:
            rejected.append(UploadIssue(filename=filename, reason=str(exc)))
            continue
        image = deps.repo.add_image(
            task_id,
            position,
            filename,
            ingested.sha256,
            str(ingested.original_path),
            str(ingested.inference_path),
            str(ingested.thumbnail_path),
        )
        accepted.append(UploadAccepted(id=image.id, filename=filename, sha256=image.sha256))
        position += 1
    deps.session.commit()
    return UploadSummary(accepted=accepted, skipped=skipped, rejected=rejected)


@router.get("/{task_id}/images/{image_id}/content")
def image_content(
    task_id: str,
    image_id: str,
    variant: Literal["original", "thumbnail"],
    deps: Annotated[TaskDependencies, Depends(get_task_dependencies)],
) -> FileResponse:
    try:
        image = deps.repo.get_task_image(task_id, image_id)
    except LookupError as exc:
        raise _not_found(image_id) from exc
    candidate = image.original_path if variant == "original" else image.thumbnail_path
    path = require_owned_path(candidate, deps.data_dir)
    media_type = "image/jpeg" if variant == "thumbnail" else None
    return FileResponse(path, media_type=media_type, filename=image.original_name)


def require_owned_path(candidate: str, data_dir: Path) -> Path:
    try:
        resolved = Path(candidate).resolve(strict=True)
        root = data_dir.resolve(strict=True)
    except FileNotFoundError as exc:
        raise _not_found(candidate) from exc
    if root != resolved and root not in resolved.parents:
        raise ApiError(403, "image_path_outside_data_dir", "图片路径不属于应用数据目录。")
    return resolved


def _action_error(exc: Exception) -> ApiError:
    return ApiError(409, "invalid_task_state", str(exc))


@router.post("/{task_id}/start", response_model=TaskActionAccepted, status_code=202)
async def start_task(
    task_id: str,
    deps: Annotated[TaskDependencies, Depends(get_task_dependencies)],
) -> TaskActionAccepted:
    try:
        if deps.repo.count_images(task_id) == 0:
            raise ApiError(409, "task_has_no_images", "请先上传至少一张图片。")
        deps.runner.start(task_id)
    except LookupError as exc:
        raise _not_found(task_id) from exc
    except (InvalidTransition, TaskAlreadyRunning) as exc:
        raise _action_error(exc) from exc
    return TaskActionAccepted(task_id=task_id)


@router.post("/{task_id}/pause", response_model=TaskActionAccepted, status_code=202)
async def pause_task(
    task_id: str,
    deps: Annotated[TaskDependencies, Depends(get_task_dependencies)],
) -> TaskActionAccepted:
    try:
        deps.runner.pause(task_id)
    except LookupError as exc:
        raise _not_found(task_id) from exc
    except InvalidTransition as exc:
        raise _action_error(exc) from exc
    return TaskActionAccepted(task_id=task_id)


@router.post("/{task_id}/resume", response_model=TaskActionAccepted, status_code=202)
async def resume_task(
    task_id: str,
    deps: Annotated[TaskDependencies, Depends(get_task_dependencies)],
) -> TaskActionAccepted:
    try:
        await deps.runner.wait_until_idle(task_id)
        deps.runner.resume(task_id)
    except LookupError as exc:
        raise _not_found(task_id) from exc
    except (InvalidTransition, TaskAlreadyRunning) as exc:
        raise _action_error(exc) from exc
    return TaskActionAccepted(task_id=task_id)


@router.post("/{task_id}/cancel", response_model=TaskActionAccepted, status_code=202)
async def cancel_task(
    task_id: str,
    deps: Annotated[TaskDependencies, Depends(get_task_dependencies)],
) -> TaskActionAccepted:
    try:
        deps.runner.cancel(task_id)
    except LookupError as exc:
        raise _not_found(task_id) from exc
    except InvalidTransition as exc:
        raise _action_error(exc) from exc
    return TaskActionAccepted(task_id=task_id)


@router.post(
    "/{task_id}/images/{image_id}/retry",
    response_model=TaskActionAccepted,
    status_code=202,
)
async def retry_image(
    task_id: str,
    image_id: str,
    deps: Annotated[TaskDependencies, Depends(get_task_dependencies)],
) -> TaskActionAccepted:
    try:
        deps.repo.get_task_image(task_id, image_id)
        await deps.runner.wait_until_idle(task_id)
        deps.runner.retry_image(image_id)
    except LookupError as exc:
        raise _not_found(image_id) from exc
    except (InvalidTransition, TaskAlreadyRunning) as exc:
        raise _action_error(exc) from exc
    return TaskActionAccepted(task_id=task_id)


@router.get("/{task_id}/events")
async def task_events(
    task_id: str,
    deps: Annotated[TaskDependencies, Depends(get_task_dependencies)],
    broker: Annotated[TaskEventBroker, Depends(get_event_broker)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> EventSourceResponse:
    try:
        deps.repo.get_task(task_id)
    except LookupError as exc:
        raise _not_found(task_id) from exc

    async def stream():
        async with broker.subscribe(task_id, after_id=last_event_id) as queue:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield ServerSentEvent(
                        data=json.dumps(dict(event), ensure_ascii=False),
                        event=str(event["type"]),
                        id=event.event_id,
                    )
                except TimeoutError:
                    yield ServerSentEvent(comment="heartbeat")

    return EventSourceResponse(stream())
