from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.secrets import SecretStoreProtocol


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as session:
        yield session


def get_secret_store(request: Request) -> SecretStoreProtocol:
    return request.app.state.secret_store
