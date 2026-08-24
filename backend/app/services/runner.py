import asyncio
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, cast

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.domain.results import InspectionResult
from app.domain.states import ImageStatus, InvalidTransition, TaskStatus
from app.models import TaskImage
from app.repositories import TaskRepository
from app.services.ark import (
    ArkRequestError,
    ArkRuntimeConfig,
    InspectionServiceProtocol,
    MalformedInspectionResult,
)
from app.services.events import TaskEventBroker
from app.services.prompts import build_prompt
from app.services.retry import RetryPolicy


class InspectionFailed(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PauseTaskError(InspectionFailed):
    pass


class TaskAlreadyRunning(RuntimeError):
    pass


MAX_TASK_CONCURRENCY = 500


class TaskRunner:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        inspector: InspectionServiceProtocol,
        concurrency: int = 3,
        events: TaskEventBroker | None = None,
        sleep: Callable[[float], None] = time.sleep,
        report_callback: Callable[[str], Awaitable[object]] | None = None,
    ) -> None:
        if not 1 <= concurrency <= MAX_TASK_CONCURRENCY:
            raise ValueError("concurrency must be between 1 and 500")
        self.sessions = sessions
        self.inspector = inspector
        self.concurrency = concurrency
        self.events = events or TaskEventBroker()
        self.sleep = sleep
        self.report_callback = report_callback
        self._active_tasks: dict[str, asyncio.Task[None]] = {}

    async def run_task(self, task_id: str) -> None:
        self._mark_running(task_id)
        concurrency = self._task_concurrency(task_id)
        executor = ThreadPoolExecutor(max_workers=concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        workers = [
            asyncio.create_task(self._worker(task_id, semaphore, executor))
            for _ in range(concurrency)
        ]
        try:
            await asyncio.gather(*workers)
            all_succeeded = self._finalize_if_drained(task_id)
            if all_succeeded is not None:
                await self.events.publish(
                    task_id,
                    {
                        "type": "task.updated",
                        "task_id": task_id,
                        "status": TaskStatus.REVIEW_REQUIRED.value,
                    },
                )
                if all_succeeded and self.report_callback is not None:
                    export_id = await self.report_callback(task_id)
                    if export_id is not None:
                        await self.events.publish(
                            task_id,
                            {
                                "type": "task.updated",
                                "task_id": task_id,
                                "status": TaskStatus.EXPORTING.value,
                            },
                        )
        finally:
            for worker in workers:
                if not worker.done():
                    worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            shutdown_task = asyncio.create_task(
                asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
            )
            try:
                await asyncio.shield(shutdown_task)
            except asyncio.CancelledError:
                await shutdown_task
                raise

    def start(self, task_id: str) -> None:
        active = self._active_tasks.get(task_id)
        if active is not None and not active.done():
            raise TaskAlreadyRunning(task_id)
        with self.sessions() as session:
            repo = TaskRepository(session)
            task = repo.get_task(task_id)
            status = TaskStatus(task.status)
            if status is TaskStatus.DRAFT:
                repo.set_task_status(task_id, TaskStatus.QUEUED)
            elif status is not TaskStatus.QUEUED:
                raise InvalidTransition(f"cannot start task from {status}")
            session.commit()
        running = asyncio.create_task(self.run_task(task_id))
        self._active_tasks[task_id] = running
        running.add_done_callback(lambda finished: self._discard_finished(task_id, finished))

    def pause(self, task_id: str) -> None:
        with self.sessions() as session:
            repo = TaskRepository(session)
            task = repo.get_task(task_id)
            if TaskStatus(task.status) in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
                repo.set_task_status(task_id, TaskStatus.PAUSED)
            else:
                raise InvalidTransition(f"cannot pause task from {task.status}")
            session.commit()

    def resume(self, task_id: str) -> None:
        with self.sessions() as session:
            repo = TaskRepository(session)
            repo.set_task_status(task_id, TaskStatus.QUEUED)
            session.commit()
        self.start(task_id)

    def cancel(self, task_id: str) -> None:
        with self.sessions() as session:
            repo = TaskRepository(session)
            repo.set_task_status(task_id, TaskStatus.CANCELED)
            session.execute(
                update(TaskImage)
                .where(
                    TaskImage.task_id == task_id,
                    TaskImage.status == ImageStatus.QUEUED.value,
                )
                .values(status=ImageStatus.CANCELED.value)
            )
            session.commit()

    def retry_image(self, image_id: str) -> None:
        with self.sessions() as session:
            repo = TaskRepository(session)
            image = repo.get_image(image_id)
            if ImageStatus(image.status) is not ImageStatus.FAILED:
                raise InvalidTransition(f"cannot retry image from {image.status}")
            task = repo.get_task(image.task_id)
            task_status = TaskStatus(task.status)
            if task_status not in {TaskStatus.PAUSED, TaskStatus.REVIEW_REQUIRED}:
                raise InvalidTransition(f"cannot retry image for task in {task.status}")
            image.status = ImageStatus.QUEUED.value
            image.error_code = None
            image.error_message = None
            repo.set_task_status(task.id, TaskStatus.QUEUED)
            session.commit()
        self.start(image.task_id)

    async def wait_until_idle(self, task_id: str) -> None:
        active = self._active_tasks.get(task_id)
        if active is not None and not active.done():
            await asyncio.shield(active)

    async def shutdown(self) -> None:
        running = [task for task in self._active_tasks.values() if not task.done()]
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        self._active_tasks.clear()

    async def _worker(
        self,
        task_id: str,
        semaphore: asyncio.Semaphore,
        executor: ThreadPoolExecutor,
    ) -> None:
        while self._task_is_running(task_id):
            image = self._claim_next_image(task_id)
            if image is None:
                return
            await self.events.publish(
                task_id,
                {"type": "image.started", "task_id": task_id, "image_id": image.id},
            )
            try:
                async with semaphore:
                    result = await asyncio.get_running_loop().run_in_executor(
                        executor,
                        self._inspect_with_retry,
                        task_id,
                        image,
                    )
                self._save_success(image.id, result)
                await self.events.publish(
                    task_id,
                    {"type": "image.succeeded", "task_id": task_id, "image_id": image.id},
                )
            except PauseTaskError as exc:
                self._save_failure_and_pause(task_id, image.id, exc)
                await self.events.publish(
                    task_id,
                    {"type": "task.updated", "task_id": task_id, "status": "paused"},
                )
                return
            except InspectionFailed as exc:
                self._save_failure(image.id, exc)
                await self.events.publish(
                    task_id,
                    {"type": "image.failed", "task_id": task_id, "image_id": image.id},
                )

    def _mark_running(self, task_id: str) -> None:
        with self.sessions() as session:
            repo = TaskRepository(session)
            task = repo.get_task(task_id)
            if TaskStatus(task.status) is TaskStatus.QUEUED:
                repo.set_task_status(task_id, TaskStatus.RUNNING)
            elif TaskStatus(task.status) is not TaskStatus.RUNNING:
                raise InvalidTransition(f"cannot run task from {task.status}")
            session.commit()

    def _task_concurrency(self, task_id: str) -> int:
        with self.sessions() as session:
            task = TaskRepository(session).get_task(task_id)
            configured = int(task.model_snapshot_json.get("concurrency", self.concurrency))
        return configured if 1 <= configured <= MAX_TASK_CONCURRENCY else self.concurrency

    def _task_is_running(self, task_id: str) -> bool:
        with self.sessions() as session:
            return TaskStatus(TaskRepository(session).get_task(task_id).status) is TaskStatus.RUNNING

    def _claim_next_image(self, task_id: str) -> TaskImage | None:
        with self.sessions() as session:
            image = TaskRepository(session).claim_next_image(task_id)
            session.commit()
            return image

    def _inspect_with_retry(self, task_id: str, image: TaskImage) -> InspectionResult:
        config, system_prompt, user_prompt = self._inspection_context(task_id, image)
        policy = RetryPolicy(config.max_retries)
        retry_index = 0
        attempt_number = image.attempt_count
        while True:
            if retry_index > 0:
                attempt_number = self._reserve_retry_attempt(image.id)
            started = time.monotonic()
            try:
                outcome = self.inspector.inspect(
                    config,
                    system_prompt,
                    user_prompt,
                    Path(image.inference_path),
                )
            except ArkRequestError as exc:
                self._record_attempt(
                    image.id,
                    status="failed",
                    attempt_number=attempt_number,
                    status_code=exc.status_code,
                    error_code=exc.code or "ark_request_failed",
                    duration_ms=self._duration_ms(started),
                    usage=(
                        exc.usage.model_dump(mode="json")
                        if exc.usage is not None
                        else None
                    ),
                )
                decision = policy.classify(exc.status_code, exc.headers, retry_index)
                if decision.pause_task:
                    raise PauseTaskError(
                        exc.code or "ark_configuration_error",
                        "Ark request requires configuration review",
                    ) from exc
                if not decision.retry:
                    raise InspectionFailed(
                        exc.code or "ark_request_failed",
                        "Ark request failed after retries",
                    ) from exc
                self.sleep(decision.delay_seconds)
                retry_index += 1
                continue
            except MalformedInspectionResult as exc:
                self._record_attempt(
                    image.id,
                    status="failed",
                    attempt_number=attempt_number,
                    error_code="invalid_model_output",
                    duration_ms=self._duration_ms(started),
                    usage=(
                        exc.usage.model_dump(mode="json")
                        if exc.usage is not None
                        else None
                    ),
                )
                raise InspectionFailed("invalid_model_output", str(exc)) from exc
            self._record_attempt(
                image.id,
                status="succeeded",
                attempt_number=attempt_number,
                duration_ms=self._duration_ms(started),
                usage=outcome.usage.model_dump(mode="json"),
            )
            return outcome.result

    def _inspection_context(
        self,
        task_id: str,
        image: TaskImage,
    ) -> tuple[ArkRuntimeConfig, str, str]:
        with self.sessions() as session:
            task = TaskRepository(session).get_task(task_id)
            model = task.model_snapshot_json
            template = task.template_snapshot_json
        config = ArkRuntimeConfig(
            base_url=str(model["base_url"]),
            model=str(model["model"]),
            protocol=cast(Literal["responses", "chat"], model["protocol"]),
            thinking=cast(Literal["disabled", "auto", "enabled"], model["thinking"]),
            timeout_seconds=int(model["timeout_seconds"]),
            max_retries=int(model["max_retries"]),
        )
        prompt = build_prompt(template, image.original_name)
        return config, prompt.system, prompt.user

    def _record_attempt(
        self,
        image_id: str,
        status: str,
        attempt_number: int,
        duration_ms: int,
        status_code: int | None = None,
        error_code: str | None = None,
        usage: dict[str, object] | None = None,
    ) -> None:
        with self.sessions() as session:
            TaskRepository(session).record_attempt(
                image_id,
                status=status,
                attempt_number=attempt_number,
                status_code=status_code,
                error_code=error_code,
                duration_ms=duration_ms,
                usage=usage,
            )
            session.commit()

    def _reserve_retry_attempt(self, image_id: str) -> int:
        with self.sessions() as session:
            attempt_number = TaskRepository(session).reserve_retry_attempt(image_id)
            session.commit()
            return attempt_number

    def _save_success(self, image_id: str, result: InspectionResult) -> None:
        with self.sessions() as session:
            repo = TaskRepository(session)
            repo.save_result(image_id, result.model_dump(mode="json"), raw_text_redacted="")
            image = repo.set_image_status(image_id, ImageStatus.SUCCEEDED)
            image.error_code = None
            image.error_message = None
            session.commit()

    def _save_failure(self, image_id: str, error: InspectionFailed) -> None:
        with self.sessions() as session:
            repo = TaskRepository(session)
            image = repo.set_image_status(image_id, ImageStatus.FAILED)
            image.error_code = error.code
            image.error_message = str(error)
            session.commit()

    def _save_failure_and_pause(
        self,
        task_id: str,
        image_id: str,
        error: PauseTaskError,
    ) -> None:
        with self.sessions() as session:
            repo = TaskRepository(session)
            image = repo.set_image_status(image_id, ImageStatus.FAILED)
            image.error_code = error.code
            image.error_message = str(error)
            task = repo.get_task(task_id)
            if TaskStatus(task.status) is TaskStatus.RUNNING:
                repo.set_task_status(task_id, TaskStatus.PAUSED)
            session.commit()

    def _finalize_if_drained(self, task_id: str) -> bool | None:
        with self.sessions() as session:
            repo = TaskRepository(session)
            task = repo.get_task(task_id)
            if TaskStatus(task.status) is not TaskStatus.RUNNING:
                return None
            statuses = list(
                session.scalars(
                    select(TaskImage.status)
                    .where(TaskImage.task_id == task_id)
                    .order_by(TaskImage.position)
                )
            )
            if any(
                status in {ImageStatus.QUEUED.value, ImageStatus.RUNNING.value}
                for status in statuses
            ):
                return None
            all_succeeded = bool(statuses) and all(
                status == ImageStatus.SUCCEEDED.value for status in statuses
            )
            repo.set_task_status(task_id, TaskStatus.REVIEW_REQUIRED)
            session.commit()
            return all_succeeded

    @staticmethod
    def _duration_ms(started: float) -> int:
        return round((time.monotonic() - started) * 1000)

    def _discard_finished(self, task_id: str, finished: asyncio.Task[None]) -> None:
        if self._active_tasks.get(task_id) is finished:
            self._active_tasks.pop(task_id, None)
