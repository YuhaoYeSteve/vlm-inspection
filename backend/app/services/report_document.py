import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from app.domain.usage import TokenUsageSummary, UsageAvailability

SOURCE_NAME = re.compile(
    r"^行\d+_[A-Z]+列_(?P<department>.+)_(?P<sequence>\d+)\.[^.]+$",
    re.IGNORECASE,
)
MAX_IMAGES_PER_BAND = 12
RESULT_COLORS = {
    "pass": "green",
    "fail": "red",
    "unverifiable": "yellow",
    "failed": "red",
}
VERDICT_LABELS = {
    "pass": "合格",
    "fail": "不合格",
    "unverifiable": "无法判断",
    "failed": "执行失败",
}
FINDING_STATUS_LABELS = {
    "pass": "合格",
    "fail": "不合格",
    "not_visible": "未清晰入镜",
}
EXECUTION_STATUS_LABELS = {
    "failed": "执行失败",
}


@dataclass(frozen=True)
class DocumentFinding:
    rule_name: str
    status: str
    observation: str
    visual_evidence: str


@dataclass(frozen=True)
class DocumentItem:
    image_id: str
    filename: str
    image_path: Path
    verdict: str
    summary: str
    image_valid: bool | None
    unverifiable_reason: str | None
    execution_status: str
    findings: tuple[DocumentFinding, ...]


@dataclass(frozen=True)
class DocumentImagePlacement:
    operation_key: str
    image_id: str
    path: Path
    filename: str


@dataclass(frozen=True)
class DocumentLayout:
    xml: str
    images: tuple[DocumentImagePlacement, ...]


def _department(filename: str) -> str:
    match = SOURCE_NAME.match(filename)
    return match.group("department") if match else "巡检图片"


def _department_groups(items: tuple[DocumentItem, ...]) -> list[tuple[str, int]]:
    groups: list[tuple[str, int]] = []
    for item in items:
        department = _department(item.filename)
        if groups and groups[-1][0] == department:
            groups[-1] = (department, groups[-1][1] + 1)
        else:
            groups.append((department, 1))
    return groups


def _format_tokens(value: int) -> str:
    return f"{value:,}"


def _token_usage_text(token_usage: TokenUsageSummary) -> str:
    if token_usage.availability is UsageAvailability.NOT_STARTED:
        return "尚无模型调用。"
    if token_usage.availability is UsageAvailability.UNTRACKED:
        lines = ["该任务运行时尚未采集 Token 用量。"]
        if token_usage.unknown_call_count > 0:
            lines.append(
                "未采集 usage：至少 "
                f"{_format_tokens(token_usage.unknown_call_count)} 次。"
            )
        return "\n".join(lines)
    if token_usage.availability is UsageAvailability.UNAVAILABLE:
        lines = ["没有可用的 Token 用量（未返回或格式异常）。"]
        if token_usage.unknown_call_count > 0:
            lines.append(
                "未返回或格式异常 usage：至少 "
                f"{_format_tokens(token_usage.unknown_call_count)} 次。"
            )
        return "\n".join(lines)

    total_label = (
        "已知总 Token"
        if token_usage.availability is UsageAvailability.PARTIAL
        else "总 Token"
    )
    lines = [
        f"{total_label}：{_format_tokens(token_usage.total_tokens)}",
        f"输入：{_format_tokens(token_usage.input_tokens)}",
        f"缓存命中：{_format_tokens(token_usage.cached_input_tokens)}",
        f"未缓存输入：{_format_tokens(token_usage.uncached_input_tokens)}",
        f"输出：{_format_tokens(token_usage.output_tokens)}",
        f"推理 Token：{_format_tokens(token_usage.reasoning_tokens)}（包含在输出中）",
        f"已知调用：{_format_tokens(token_usage.known_call_count)}",
    ]
    if token_usage.unknown_call_count > 0:
        lines.append(
            "未返回、格式异常或未采集 usage：至少 "
            f"{_format_tokens(token_usage.unknown_call_count)} "
            "次；不计入已知总量。"
        )
    return "\n".join(lines)


def _result_parts(item: DocumentItem) -> tuple[str, str]:
    try:
        label = VERDICT_LABELS[item.verdict]
    except KeyError as exc:
        raise ValueError(f"unsupported verdict: {item.verdict}") from exc
    if item.verdict == "pass":
        return label, ""
    if item.verdict == "fail":
        return label, item.summary.strip()
    if item.verdict == "unverifiable":
        return label, (item.unverifiable_reason or item.summary).strip()
    return label, item.execution_status.strip()


def _result_cell(item: DocumentItem) -> str:
    label, detail = _result_parts(item)
    detail_xml = f"<br/>{html.escape(detail)}" if detail else ""
    return (
        '<td vertical-align="middle"><p align="center">'
        f'<b><span text-color="{RESULT_COLORS[item.verdict]}">'
        f"{label}{detail_xml}</span></b></p></td>"
    )


def _model_output_cell(item: DocumentItem) -> str:
    if item.image_valid is None:
        status = EXECUTION_STATUS_LABELS.get(
            item.execution_status,
            item.execution_status,
        )
        return (
            '<td vertical-align="top"><p><b>无模型输出</b></p>'
            f"<p><b>执行状态：</b>{html.escape(status)}</p></td>"
        )

    verdict = VERDICT_LABELS.get(item.verdict, item.verdict)
    parts = [
        f"<p><b>图片有效性：</b>{'有效' if item.image_valid else '无效'}</p>",
        f"<p><b>总体结论：</b>{html.escape(verdict)}</p>",
        f"<p><b>总结：</b>{html.escape(item.summary)}</p>",
    ]
    if item.unverifiable_reason:
        parts.append(
            "<p><b>不可判断原因：</b>"
            f"{html.escape(item.unverifiable_reason)}</p>"
        )
    if not item.findings:
        parts.append("<p><b>检查项：</b>无</p>")
    for index, finding in enumerate(item.findings, start=1):
        status = FINDING_STATUS_LABELS.get(finding.status, finding.status)
        parts.extend(
            (
                (
                    "<p><b>"
                    f"检查项 {index}：{html.escape(finding.rule_name)}｜"
                    f"{html.escape(status)}</b></p>"
                ),
                f"<p><b>观察：</b>{html.escape(finding.observation)}</p>",
                (
                    "<p><b>视觉证据：</b>"
                    f"{html.escape(finding.visual_evidence)}</p>"
                ),
            )
        )
    return f'<td vertical-align="top">{"".join(parts)}</td>'


def _render_band(items: tuple[DocumentItem, ...]) -> str:
    group_headers = "".join(
        (
            f'<th colspan="{size}" background-color="light-gray" '
            f'vertical-align="middle"><p align="center"><b>{html.escape(name)}</b>'
            "</p></th>"
        )
        for name, size in _department_groups(items)
    )
    image_cells = "".join(
        '<td vertical-align="middle"><p></p></td>' for _item in items
    )
    result_cells = "".join(_result_cell(item) for item in items)
    model_output_cells = "".join(_model_output_cell(item) for item in items)
    return (
        "<table>"
        f'<colgroup><col width="110"/><col span="{len(items)}" width="240"/></colgroup>'
        "<thead><tr>"
        '<th background-color="light-gray" vertical-align="middle">'
        '<p align="center"><b>巡检项</b></p></th>'
        f"{group_headers}</tr></thead>"
        "<tbody>"
        "<tr>"
        '<td background-color="light-gray" vertical-align="middle">'
        '<p align="center"><b>巡检图片</b></p></td>'
        f"{image_cells}</tr>"
        "<tr>"
        '<td background-color="light-gray" vertical-align="middle">'
        '<p align="center"><b>巡检结果</b></p></td>'
        f"{result_cells}</tr>"
        "<tr>"
        '<td background-color="light-gray" vertical-align="middle">'
        '<p align="center"><b>模型完整输出</b></p></td>'
        f"{model_output_cells}</tr>"
        "</tbody></table>"
    )


def _render_usage(token_usage: TokenUsageSummary) -> str:
    usage = html.escape(_token_usage_text(token_usage)).replace("\n", "<br/>")
    return (
        "<table><colgroup><col width=\"1370\"/></colgroup><tbody>"
        '<tr><td background-color="light-gray" vertical-align="middle">'
        "<p><b>模型 Token 用量</b></p></td></tr>"
        f'<tr><td vertical-align="top"><p>{usage}</p></td></tr>'
        "</tbody></table>"
    )


def render_report_document(
    items: tuple[DocumentItem, ...],
    token_usage: TokenUsageSummary,
    max_images_per_band: int = MAX_IMAGES_PER_BAND,
) -> DocumentLayout:
    if not items:
        raise ValueError("report requires at least one image")
    if max_images_per_band < 1:
        raise ValueError("max_images_per_band must be positive")

    bands = [
        _render_band(items[start : start + max_images_per_band])
        for start in range(0, len(items), max_images_per_band)
    ]
    images = tuple(
        DocumentImagePlacement(
            operation_key=f"doc-image-bound:{item.image_id}",
            image_id=item.image_id,
            path=item.image_path,
            filename=item.filename,
        )
        for item in items
    )
    return DocumentLayout("".join((*bands, _render_usage(token_usage))), images)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct_children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def _table_rows(table: ET.Element) -> list[ET.Element]:
    rows: list[ET.Element] = []
    for section in table:
        section_name = _local_name(section.tag)
        if section_name == "tr":
            rows.append(section)
        elif section_name in {"thead", "tbody"}:
            rows.extend(_direct_children(section, "tr"))
    return rows


def _cell_text(cell: ET.Element) -> str:
    return "".join(cell.itertext()).strip()


def _cell_anchor_id(cell: ET.Element) -> str | None:
    for descendant in cell.iter():
        if descendant is cell:
            continue
        block_id = descendant.attrib.get("id")
        if block_id:
            return block_id
    return cell.attrib.get("id")


def parse_image_cell_ids(content: str, expected_count: int) -> tuple[str, ...]:
    if expected_count < 0:
        raise ValueError("expected_count must not be negative")
    try:
        root = ET.fromstring(f"<report-root>{content}</report-root>")
    except ET.ParseError as exc:
        raise ValueError("invalid document XML") from exc

    cell_ids: list[str] = []
    for table in (element for element in root.iter() if _local_name(element.tag) == "table"):
        for row in _table_rows(table):
            cells = [
                child
                for child in row
                if _local_name(child.tag) in {"td", "th"}
            ]
            if not cells or _cell_text(cells[0]) != "巡检图片":
                continue
            cell_ids.extend(
                cell_id
                for cell in cells[1:]
                if (cell_id := _cell_anchor_id(cell))
            )

    if len(cell_ids) != expected_count:
        raise ValueError(
            f"expected {expected_count} image cells, found {len(cell_ids)}"
        )
    return tuple(cell_ids)
