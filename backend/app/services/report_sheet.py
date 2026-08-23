import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SOURCE_NAME = re.compile(
    r"^行\d+_[A-Z]+列_(?P<department>.+)_(?P<sequence>\d+)\.[^.]+$",
    re.IGNORECASE,
)
MAX_IMAGES_PER_BAND = 12
HEADER_FILL = "#DEE0E3"
BORDER = {"style": "solid", "weight": "thin", "color": "#BBBFC4"}
RESULT_COLORS = {
    "pass": "#238636",
    "fail": "#D1242F",
    "unverifiable": "#B26A00",
    "failed": "#D1242F",
}


@dataclass(frozen=True)
class SheetItem:
    image_id: str
    filename: str
    image_path: Path
    verdict: str
    summary: str
    unverifiable_reason: str | None
    execution_status: str


@dataclass(frozen=True)
class SheetImagePlacement:
    operation_key: str
    cell: str
    path: Path
    filename: str


@dataclass(frozen=True)
class SpreadsheetLayout:
    values: tuple[tuple[str, ...], ...]
    styles: dict[str, Any]
    images: tuple[SheetImagePlacement, ...]


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def _department(filename: str) -> str:
    match = SOURCE_NAME.match(filename)
    return match.group("department") if match else "巡检图片"


def _result_text(item: SheetItem) -> str:
    if item.verdict == "pass":
        return "合格"
    if item.verdict == "fail":
        return _with_detail("不合格", item.summary)
    if item.verdict == "unverifiable":
        return _with_detail("无法判断", item.unverifiable_reason or item.summary)
    if item.verdict == "failed":
        return _with_detail("执行失败", item.execution_status)
    raise ValueError(f"unsupported verdict: {item.verdict}")


def _with_detail(label: str, detail: str) -> str:
    normalized = detail.strip()
    return f"{label}\n{normalized}" if normalized else label


def _department_groups(items: tuple[SheetItem, ...]) -> list[tuple[str, int, int]]:
    groups: list[tuple[str, int, int]] = []
    for offset, item in enumerate(items, start=2):
        department = _department(item.filename)
        if groups and groups[-1][0] == department:
            name, start, _end = groups[-1]
            groups[-1] = (name, start, offset)
        else:
            groups.append((department, offset, offset))
    return groups


def render_report_sheet(
    items: tuple[SheetItem, ...],
    max_images_per_band: int = MAX_IMAGES_PER_BAND,
) -> SpreadsheetLayout:
    if not items:
        raise ValueError("report requires at least one image")
    if max_images_per_band < 1:
        raise ValueError("max_images_per_band must be positive")

    widest_band = min(len(items), max_images_per_band)
    total_columns = widest_band + 1
    last_column = _column_name(total_columns)
    values: list[tuple[str, ...]] = []
    images: list[SheetImagePlacement] = []
    cell_styles: list[dict[str, Any]] = []
    cell_merges: list[dict[str, str]] = []
    row_sizes: list[dict[str, str | int]] = []

    for band_index, start in enumerate(range(0, len(items), max_images_per_band)):
        band = items[start : start + max_images_per_band]
        header_row = band_index * 3 + 1
        image_row = header_row + 1
        result_row = header_row + 2
        band_last_column = _column_name(len(band) + 1)

        header = ["巡检项"]
        for department, group_start, group_end in _department_groups(band):
            header.append(department)
            header.extend("" for _ in range(group_end - group_start))
            if group_end > group_start:
                cell_merges.append(
                    {
                        "range": (
                            f"{_column_name(group_start)}{header_row}:"
                            f"{_column_name(group_end)}{header_row}"
                        ),
                        "merge_type": "all",
                    }
                )
        image_values = ["巡检图片", *("" for _ in band)]
        result_values = ["巡检结果", *(_result_text(item) for item in band)]
        for row in (header, image_values, result_values):
            values.append((*row, *("" for _ in range(total_columns - len(row)))))

        band_range = f"A{header_row}:{band_last_column}{result_row}"
        cell_styles.extend(
            [
                {
                    "range": band_range,
                    "horizontal_alignment": "center",
                    "vertical_alignment": "middle",
                    "border": BORDER,
                },
                {
                    "range": f"A{header_row}:{band_last_column}{header_row}",
                    "background_color": HEADER_FILL,
                    "font_weight": "bold",
                    "horizontal_alignment": "center",
                    "vertical_alignment": "middle",
                    "border": BORDER,
                },
                {
                    "range": f"A{header_row}:A{result_row}",
                    "background_color": HEADER_FILL,
                    "font_weight": "bold",
                    "horizontal_alignment": "center",
                    "vertical_alignment": "middle",
                    "border": BORDER,
                },
                {
                    "range": f"B{result_row}:{band_last_column}{result_row}",
                    "horizontal_alignment": "center",
                    "vertical_alignment": "middle",
                    "word_wrap": "auto-wrap",
                    "border": BORDER,
                },
            ]
        )
        for offset, item in enumerate(band, start=2):
            column = _column_name(offset)
            images.append(
                SheetImagePlacement(
                    operation_key=f"sheet-image:{item.image_id}",
                    cell=f"{column}{image_row}",
                    path=item.image_path,
                    filename=item.filename,
                )
            )
            cell_styles.append(
                {
                    "range": f"{column}{result_row}",
                    "font_color": RESULT_COLORS[item.verdict],
                }
            )
        row_sizes.extend(
            [
                {"range": f"{header_row}:{header_row}", "type": "pixel", "size": 38},
                {"range": f"{image_row}:{image_row}", "type": "pixel", "size": 124},
                {"range": f"{result_row}:{result_row}", "type": "auto"},
            ]
        )

    styles = {
        "styles": [
            {
                "name": "Sheet1",
                "cell_styles": cell_styles,
                "cell_merges": cell_merges,
                "row_sizes": row_sizes,
                "col_sizes": [
                    {"range": "A:A", "type": "pixel", "size": 110},
                    {
                        "range": f"B:{last_column}",
                        "type": "pixel",
                        "size": 105,
                    },
                ],
            }
        ]
    }
    return SpreadsheetLayout(tuple(values), styles, tuple(images))
