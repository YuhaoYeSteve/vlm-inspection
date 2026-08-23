from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine

from app.api.errors import ApiError
from app.api.feishu_auth import router as feishu_auth_router
from app.api.settings import router as settings_router
from app.api.tasks import router as tasks_router
from app.api.templates import router as templates_router
from app.api.templates import seed_templates_if_empty
from app.core.config import Settings
from app.core.db import make_engine, make_session_factory
from app.core.secrets import KeyringSecretStore, SecretStoreProtocol
from app.repositories import TaskRepository
from app.services.ark import InspectionServiceProtocol, KeychainArkInspectionService
from app.services.events import TaskEventBroker
from app.services.feishu_client import FeishuClientProtocol
from app.services.lark_cli import (
    FeishuConnectionProviderProtocol,
    FeishuDestination,
    LarkCliCommandRunner,
    LarkCliConnectionProvider,
    LarkCliFeishuClient,
    LarkCliRunnerProtocol,
)
from app.services.reports import ReportBuilder, ReportCoordinator
from app.services.runner import TaskRunner


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    secret_store: SecretStoreProtocol | None = None,
    inspection_service: InspectionServiceProtocol | None = None,
    lark_cli_runner: LarkCliRunnerProtocol | None = None,
    feishu_connection_provider: FeishuConnectionProviderProtocol | None = None,
    feishu_client: FeishuClientProtocol | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    if engine is None:
        resolved_settings.ensure_directories()
    resolved_engine = engine or make_engine(resolved_settings.database_path)
    sessions = make_session_factory(resolved_engine)
    resolved_secret_store = secret_store or KeyringSecretStore()
    resolved_inspection_service = inspection_service or KeychainArkInspectionService(
        resolved_secret_store
    )
    events = TaskEventBroker()
    destination = FeishuDestination(
        parent_token=resolved_settings.feishu_parent_token,
        path=resolved_settings.feishu_parent_label,
        reference_url=resolved_settings.feishu_reference_url,
    )
    resolved_lark_runner = lark_cli_runner or LarkCliCommandRunner(
        resolved_settings.lark_cli_path
    )
    resolved_connection_provider = (
        feishu_connection_provider
        or LarkCliConnectionProvider(resolved_lark_runner, destination)
    )
    resolved_feishu_client = feishu_client or LarkCliFeishuClient(resolved_lark_runner)
    report_builder = ReportBuilder(
        sessions,
        resolved_feishu_client,
        folder_token=destination.parent_token,
    )
    report_coordinator = ReportCoordinator(
        report_builder,
        resolved_connection_provider,
        events,
    )
    runner = TaskRunner(
        sessions,
        resolved_inspection_service,
        events=events,
        report_callback=report_coordinator.enqueue_auto,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with app.state.sessions() as session:
            seed_templates_if_empty(session)
            TaskRepository(session).recover_interrupted_exports()
            TaskRepository(session).recover_interrupted_work()
            session.commit()
        try:
            yield
        finally:
            await app.state.runner.shutdown()
            await app.state.report_coordinator.shutdown()

    app = FastAPI(title="Seed VLM Inspector", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.sessions = sessions
    app.state.secret_store = resolved_secret_store
    app.state.inspection_service = inspection_service
    app.state.event_broker = events
    app.state.runner = runner
    app.state.feishu_connection_provider = resolved_connection_provider
    app.state.report_coordinator = report_coordinator

    @app.exception_handler(ApiError)
    async def handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ready"}

    app.include_router(settings_router)
    app.include_router(feishu_auth_router)
    app.include_router(templates_router)
    app.include_router(tasks_router)
    mount_frontend(
        app,
        frontend_dist or Path(__file__).resolve().parents[2] / "frontend" / "dist",
    )
    return app


def mount_frontend(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.is_file():
        return
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        return FileResponse(index)


app = create_app()
