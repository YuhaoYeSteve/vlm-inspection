from typing import Protocol

import keyring
from keyring.errors import KeyringError, PasswordDeleteError


class SecretStoreProtocol(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class KeyringSecretStore:
    def __init__(self, service: str = "vlm-inspector") -> None:
        self.service = service

    def get(self, key: str) -> str | None:
        try:
            return keyring.get_password(self.service, key)
        except KeyringError as exc:
            raise RuntimeError("system keychain is unavailable") from exc

    def set(self, key: str, value: str) -> None:
        try:
            keyring.set_password(self.service, key, value)
        except KeyringError as exc:
            raise RuntimeError("system keychain is unavailable") from exc

    def delete(self, key: str) -> None:
        try:
            keyring.delete_password(self.service, key)
        except PasswordDeleteError:
            return
        except KeyringError as exc:
            raise RuntimeError("system keychain is unavailable") from exc
