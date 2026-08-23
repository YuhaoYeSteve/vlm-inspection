from dataclasses import dataclass
from typing import Protocol


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

    async def append_document_xml(
        self,
        document_id: str,
        content: str,
        operation_key: str,
    ) -> None: ...

    async def fetch_document_xml(self, document_id: str) -> str: ...

    async def create_image_block(
        self,
        document_id: str,
        parent_block_id: str,
        operation_key: str,
    ) -> BlockRef: ...

    async def upload_docx_image(
        self,
        block_id: str,
        filename: str,
        content: bytes,
    ) -> str: ...

    async def bind_docx_image(
        self,
        document_id: str,
        block_id: str,
        file_token: str,
    ) -> None: ...

    async def move_document_block(
        self,
        document_id: str,
        anchor_block_id: str,
        source_block_id: str,
    ) -> None: ...
