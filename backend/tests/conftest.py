from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.core.db import Base, make_engine
from app.main import create_app


@dataclass
class FakeSecretStore:
    values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


@dataclass
class AppHarness:
    client: TestClient
    database_path: Path
    secrets: FakeSecretStore


@pytest.fixture
def app_harness(tmp_path: Path) -> Iterator[AppHarness]:
    database_path = tmp_path / "app.db"
    engine = make_engine(database_path)
    Base.metadata.create_all(engine)
    secrets = FakeSecretStore()
    with TestClient(create_app(engine=engine, secret_store=secrets)) as client:
        yield AppHarness(client=client, database_path=database_path, secrets=secrets)
