from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app.api.errors import ApiError
from app.api.settings import router as settings_router
from app.api.templates import router as templates_router
from app.api.templates import seed_templates_if_empty
from app.core.config import Settings
from app.core.db import make_engine, make_session_factory
from app.core.secrets import KeyringSecretStore, SecretStoreProtocol
from app.repositories import TaskRepository
from app.services.ark import InspectionServiceProtocol, KeychainArkInspectionService
from app.services.events import TaskEventBroker
from app.services.runner import TaskRunner


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    secret_store: SecretStoreProtocol | None = None,
    inspection_service: InspectionServiceProtocol | None = None,
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
    runner = TaskRunner(sessions, resolved_inspection_service, events=events)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with app.state.sessions() as session:
            seed_templates_if_empty(session)
            TaskRepository(session).recover_interrupted_work()
            session.commit()
        try:
            yield
        finally:
            await app.state.runner.shutdown()

    app = FastAPI(title="Seed VLM Inspector", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.sessions = sessions
    app.state.secret_store = resolved_secret_store
    app.state.inspection_service = inspection_service
    app.state.event_broker = events
    app.state.runner = runner

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
    app.include_router(templates_router)
    return app


app = create_app()
