from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app.api.errors import ApiError
from app.api.settings import router as settings_router
from app.core.config import Settings
from app.core.db import make_engine, make_session_factory
from app.core.secrets import KeyringSecretStore, SecretStoreProtocol


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    secret_store: SecretStoreProtocol | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_settings.ensure_directories()
    resolved_engine = engine or make_engine(resolved_settings.database_path)

    app = FastAPI(title="Seed VLM Inspector", version="0.1.0")
    app.state.settings = resolved_settings
    app.state.sessions = make_session_factory(resolved_engine)
    app.state.secret_store = secret_store or KeyringSecretStore()

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
    return app


app = create_app()
