from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class SpreadsheetRef:
    id: str
    url: str
    sheet_id: str


class FeishuClientProtocol(Protocol):
    async def create_spreadsheet(
        self,
        title: str,
        folder_token: str,
        values: list[list[str]],
        styles: dict[str, Any],
    ) -> SpreadsheetRef: ...

    async def set_cell_image(
        self,
        spreadsheet_token: str,
        sheet_id: str,
        cell: str,
        image_path: Path,
        filename: str,
    ) -> None: ...
