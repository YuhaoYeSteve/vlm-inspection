from pathlib import Path

from starlette.testclient import TestClient

from app.core.config import Settings
from app.core.db import Base, make_engine
from app.main import create_app


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


def test_spa_fallback_serves_index_for_frontend_routes(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    (dist / "assets" / "app.js").write_text("export {};", encoding="utf-8")
    engine = make_engine(tmp_path / "app.db")
    Base.metadata.create_all(engine)
    app = create_app(
        settings=Settings(data_dir=tmp_path / "data"),
        engine=engine,
        secret_store=MemorySecretStore(),
        frontend_dist=dist,
    )

    with TestClient(app) as client:
        response = client.get("/history")

        assert response.status_code == 200
        assert '<div id="root"></div>' in response.text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/api/health").headers["content-type"].startswith(
            "application/json"
        )
        assert client.get("/api/unknown").status_code == 404
