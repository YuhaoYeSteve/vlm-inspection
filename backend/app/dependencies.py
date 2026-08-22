from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.secrets import SecretStoreProtocol
from app.repositories import SettingsRepository, TaskRepository
from app.schemas import ArkSettingsRead
from app.services.ark import ArkInspectionService, ArkRuntimeConfig, InspectionServiceProtocol
from app.services.events import TaskEventBroker
from app.services.feishu_oauth import FeishuOAuthProtocol
from app.services.images import ImageIngestor
from app.services.reports import ReportCoordinator
from app.services.runner import TaskRunner


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as session:
        yield session


def get_secret_store(request: Request) -> SecretStoreProtocol:
    return request.app.state.secret_store


@dataclass(frozen=True)
class ArkDependencies:
    runtime_config: ArkRuntimeConfig
    inspector: InspectionServiceProtocol
    bundled_test_image: Path


def get_ark_dependencies(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    secrets: Annotated[SecretStoreProtocol, Depends(get_secret_store)],
) -> ArkDependencies:
    stored = SettingsRepository(session).get("ark")
    if stored is None or not stored.get("model"):
        raise ApiError(409, "ark_not_configured", "请先配置火山方舟连接。")
    try:
        api_key = secrets.get("ark_api_key")
    except RuntimeError as exc:
        raise ApiError(
            503,
            "keychain_unavailable",
            "系统钥匙串不可用，请检查本机钥匙串服务。",
        ) from exc
    if api_key is None:
        raise ApiError(409, "ark_not_configured", "请先配置火山方舟 API Key。")

    settings = ArkSettingsRead(**stored, api_key_configured=True)
    runtime = ArkRuntimeConfig(
        base_url=settings.base_url,
        model=settings.model,
        protocol=settings.protocol,
        thinking=settings.thinking,
        timeout_seconds=settings.timeout_seconds,
        max_retries=settings.max_retries,
    )
    inspector = request.app.state.inspection_service or ArkInspectionService(api_key=api_key)
    return ArkDependencies(
        runtime_config=runtime,
        inspector=inspector,
        bundled_test_image=Path(__file__).parent / "assets" / "ark_test.png",
    )


@dataclass(frozen=True)
class TaskDependencies:
    session: Session
    repo: TaskRepository
    ingestor: ImageIngestor
    runner: TaskRunner
    reports: ReportCoordinator
    data_dir: Path


def get_task_dependencies(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> TaskDependencies:
    data_dir: Path = request.app.state.settings.data_dir
    return TaskDependencies(
        session=session,
        repo=TaskRepository(session),
        ingestor=ImageIngestor(data_dir / "images"),
        runner=request.app.state.runner,
        reports=request.app.state.report_coordinator,
        data_dir=data_dir,
    )


def get_event_broker(request: Request) -> TaskEventBroker:
    return request.app.state.event_broker


def get_feishu_oauth(request: Request) -> FeishuOAuthProtocol:
    return request.app.state.feishu_oauth
