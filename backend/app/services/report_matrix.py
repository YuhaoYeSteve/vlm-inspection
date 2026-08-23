import html
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SOURCE_NAME = re.compile(
    r"^行\d+_[A-Z]+列_(?P<department>.+)_(?P<sequence>\d+)\.[^.]+$",
    re.IGNORECASE,
)
VERDICT_LABELS = {
    "pass": ("合格", "green"),
    "fail": ("不合格", "red"),
    "unverifiable": ("无法判断", "yellow"),
    "failed": ("执行失败", "red"),
}


@dataclass(frozen=True)
class MatrixItem:
    filename: str
    verdict: str


@dataclass(frozen=True)
class DisplayCell:
    department: str
    sequence: str
    verdict_label: str
    verdict_color: str


def _display_cell(item: MatrixItem, index: int) -> DisplayCell:
    match = SOURCE_NAME.match(item.filename)
    department = match.group("department") if match else "巡检图片"
    sequence = match.group("sequence") if match else f"{index + 1:02d}"
    verdict_label, verdict_color = VERDICT_LABELS[item.verdict]
    return DisplayCell(department, sequence, verdict_label, verdict_color)


def _groups(cells: Iterable[DisplayCell]) -> list[tuple[str, int]]:
    groups: list[tuple[str, int]] = []
    for cell in cells:
        if groups and groups[-1][0] == cell.department:
            name, size = groups[-1]
            groups[-1] = (name, size + 1)
        else:
            groups.append((cell.department, 1))
    return groups


def _render_band(cells: list[DisplayCell]) -> str:
    headers = "".join(
        f'<th colspan="{size}" background-color="light-gray" vertical-align="middle">'
        f"{html.escape(name)}</th>"
        for name, size in _groups(cells)
    )
    results = "".join(
        '<td vertical-align="middle"><p align="center">'
        f'{html.escape(cell.sequence)}<br/><b><span text-color="{cell.verdict_color}">'
        f"{cell.verdict_label}</span></b></p></td>"
        for cell in cells
    )
    return (
        '<table><colgroup><col width="96"/>'
        f'<col span="{len(cells)}" width="82"/></colgroup>'
        '<thead><tr><th rowspan="2" background-color="light-gray" '
        f'vertical-align="middle">巡检结果</th>{headers}</tr></thead>'
        f"<tbody><tr>{results}</tr></tbody></table>"
    )


def render_result_matrix(items: tuple[MatrixItem, ...], max_columns: int = 12) -> str:
    cells = [_display_cell(item, index) for index, item in enumerate(items)]
    tables = [
        _render_band(cells[start : start + max_columns])
        for start in range(0, len(cells), max_columns)
    ]
    return "<h1>巡检结果总览</h1>" + "".join(tables)


def detail_heading(filename: str, verdict: str, index: int) -> str:
    cell = _display_cell(MatrixItem(filename, verdict), index)
    source_match = SOURCE_NAME.match(filename)
    label = f"{cell.department} {cell.sequence}" if source_match else Path(filename).stem
    return f"{label}｜{cell.verdict_label}"
