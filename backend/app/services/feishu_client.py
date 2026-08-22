import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.services.feishu_oauth import FeishuOAuthProtocol
from app.services.retry import RetryPolicy


@dataclass(frozen=True)
class DocumentRef:
    id: str
    url: str


@dataclass(frozen=True)
class BlockRef:
    id: str


class FeishuClientProtocol(Protocol):
    async def create_document(
        self,
        title: str,
        folder_token: str,
        client_token: str,
    ) -> DocumentRef: ...

    async def create_blocks(
        self,
        document_id: str,
        parent_block_id: str,
        blocks: list[dict[str, Any]],
        operation_key: str,
    ) -> list[BlockRef]: ...

    async def create_image_block(
        self,
        document_id: str,
        parent_block_id: str,
        operation_key: str,
    ) -> BlockRef: ...

    async def upload_docx_image(self, block_id: str, filename: str, content: bytes) -> str: ...


class AsyncRequestClientProtocol(Protocol):
    async def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


class FeishuApiError(RuntimeError):
    def __init__(self, code: str, status_code: int | None = None) -> None:
        super().__init__("Feishu API request failed")
        self.code = code
        self.status_code = status_code


class AsyncRateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.minimum_interval = 1 / requests_per_second
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self.minimum_interval - (time.monotonic() - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()


class FeishuClient:
    BASE_URL = "https://open.feishu.cn"

    def __init__(
        self,
        oauth: FeishuOAuthProtocol,
        http: AsyncRequestClientProtocol | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.oauth = oauth
        self.http = http
        self.sleep = sleep
        self._document_rate_limit = AsyncRateLimiter(3)
        self._media_rate_limit = AsyncRateLimiter(5)
        self._media_lock = asyncio.Lock()

    async def create_document(
        self,
        title: str,
        folder_token: str,
        client_token: str,
    ) -> DocumentRef:
        await self._document_rate_limit.wait()
        payload = await self._request(
            "POST",
            "/open-apis/docx/v1/documents",
            params={"client_token": client_token},
            json={"title": title, "folder_token": folder_token},
        )
        document = payload["data"]["document"]
        document_id = str(document["document_id"])
        url = str(document.get("url") or f"https://feishu.cn/docx/{document_id}")
        return DocumentRef(document_id, url)

    async def create_blocks(
        self,
        document_id: str,
        parent_block_id: str,
        blocks: list[dict[str, Any]],
        operation_key: str,
    ) -> list[BlockRef]:
        await self._document_rate_limit.wait()
        payload = await self._request(
            "POST",
            f"/open-apis/docx/v1/documents/{document_id}/blocks/{parent_block_id}/children",
            params={
                "document_revision_id": "-1",
                "client_token": self._client_token(operation_key),
            },
            json={"children": blocks, "index": -1},
        )
        return [BlockRef(str(block["block_id"])) for block in payload["data"]["children"]]

    async def create_image_block(
        self,
        document_id: str,
        parent_block_id: str,
        operation_key: str,
    ) -> BlockRef:
        blocks = await self.create_blocks(
            document_id,
            parent_block_id,
            [{"block_type": 27, "image": {}}],
            operation_key,
        )
        if not blocks:
            raise FeishuApiError("image_block_missing")
        return blocks[0]

    async def upload_docx_image(self, block_id: str, filename: str, content: bytes) -> str:
        async with self._media_lock:
            await self._media_rate_limit.wait()
            payload = await self._request(
                "POST",
                "/open-apis/drive/v1/medias/upload_all",
                data={
                    "file_name": filename,
                    "parent_type": "docx_image",
                    "parent_node": block_id,
                    "size": str(len(content)),
                },
                files={"file": (filename, content, "image/jpeg")},
            )
            return str(payload["data"]["file_token"])

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        policy = RetryPolicy(max_retries=2)
        attempt = 0
        while True:
            token = await self.oauth.get_valid_access_token()
            headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
            try:
                response = await self._send(method, f"{self.BASE_URL}{path}", headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                decision = policy.classify(None, {}, attempt)
                if not decision.retry:
                    raise FeishuApiError("network_error") from exc
                await self.sleep(decision.delay_seconds)
                attempt += 1
                continue

            decision = policy.classify(response.status_code, response.headers, attempt)
            if decision.retry:
                await self.sleep(decision.delay_seconds)
                attempt += 1
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise FeishuApiError("http_error", response.status_code) from exc
            payload = response.json()
            if int(payload.get("code", 0)) != 0:
                raise FeishuApiError(str(payload.get("code", "api_error")), response.status_code)
            return payload

    async def _send(self, method: str, url: str, **kwargs: Any) -> Any:
        if self.http is not None:
            return await self.http.request(method, url, **kwargs)
        async with httpx.AsyncClient(timeout=60) as client:
            return await client.request(method, url, **kwargs)

    @staticmethod
    def _client_token(operation_key: str) -> str:
        return hashlib.sha256(operation_key.encode()).hexdigest()[:32]
