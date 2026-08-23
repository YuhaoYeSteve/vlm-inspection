from pathlib import Path

import pytest

from app.domain.usage import TokenUsageSummary, UsageAvailability
from app.services.report_document import (
    DocumentItem,
    parse_image_cell_ids,
    render_report_document,
)


def item(
    index: int,
    filename: str,
    verdict: str,
    summary: str = "",
    *,
    unverifiable_reason: str | None = None,
    execution_status: str = "succeeded",
) -> DocumentItem:
    return DocumentItem(
        image_id=f"image-{index}",
        filename=filename,
        image_path=Path(f"/images/{index}.jpg"),
        verdict=verdict,
        summary=summary,
        unverifiable_reason=unverifiable_reason,
        execution_status=execution_status,
    )


def current_items() -> tuple[DocumentItem, ...]:
    names = (
        "行02_D列_安保_01.png",
        "行02_E列_安保_02.png",
        "行02_F列_工程人员_01.png",
        "行02_G列_客房_01.png",
        "行02_H列_客房_02.png",
        "行02_I列_客房_03.png",
        "行02_J列_前厅_01.png",
        "行02_K列_前厅_02.png",
        "行02_L列_前厅_03.png",
        "行02_M列_前厅_04.png",
        "行02_N列_前厅_05.png",
        "行02_O列_前厅_06.png",
    )
    return tuple(
        item(
            index,
            name,
            "fail" if index in {6, 11} else "pass",
            f"问题-{index}",
        )
        for index, name in enumerate(names, start=1)
    )


def complete_usage() -> TokenUsageSummary:
    return TokenUsageSummary(
        availability=UsageAvailability.COMPLETE,
        input_tokens=30,
        cached_input_tokens=12,
        uncached_input_tokens=18,
        output_tokens=6,
        reasoning_tokens=0,
        total_tokens=36,
        known_call_count=3,
        unknown_call_count=0,
    )


def test_render_report_document_groups_images_and_results() -> None:
    layout = render_report_document(current_items(), token_usage=complete_usage())

    assert layout.xml.count("<table>") == 2
    assert '<th colspan="2"' in layout.xml
    assert '<th colspan="3"' in layout.xml
    assert '<th colspan="6"' in layout.xml
    assert ">安保<" in layout.xml
    assert ">工程人员<" in layout.xml
    assert ">客房<" in layout.xml
    assert ">前厅<" in layout.xml
    assert layout.xml.count(">合格<") == 10
    assert layout.xml.count(">不合格<br/>") == 2
    assert "问题-6" in layout.xml
    assert "问题-11" in layout.xml
    assert [placement.image_id for placement in layout.images] == [
        f"image-{index}" for index in range(1, 13)
    ]
    assert [placement.operation_key for placement in layout.images] == [
        f"doc-image:image-{index}" for index in range(1, 13)
    ]


def test_render_report_document_escapes_cell_text() -> None:
    layout = render_report_document(
        (item(1, "普通图片.png", "fail", "工牌 < 缺失 & 待确认"),),
        token_usage=complete_usage(),
    )

    assert "工牌 &lt; 缺失 &amp; 待确认" in layout.xml
    assert ">巡检图片<" in layout.xml


def test_render_report_document_starts_new_band_after_twelve_images() -> None:
    items = tuple(
        item(index, f"行02_D列_前厅_{index:02}.png", "pass")
        for index in range(1, 14)
    )

    layout = render_report_document(items, token_usage=complete_usage())

    assert layout.xml.count("<table>") == 3
    assert layout.xml.count(">巡检图片<") == 2
    assert len(layout.images) == 13


def test_render_report_document_formats_untracked_usage() -> None:
    usage = TokenUsageSummary(
        availability=UsageAvailability.UNTRACKED,
        input_tokens=0,
        cached_input_tokens=0,
        uncached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        total_tokens=0,
        known_call_count=0,
        unknown_call_count=12,
    )

    layout = render_report_document((item(1, "one.png", "pass"),), usage)

    assert "该任务运行时尚未采集 Token 用量。" in layout.xml
    assert "未采集 usage：至少 12 次。" in layout.xml


def test_parse_image_cell_anchor_ids_reads_all_report_bands_in_order() -> None:
    fetched_xml = """
    <document>
      <table id="table-1">
        <thead><tr><th id="a1"><p>巡检项</p></th><th id="b1"><p>安保</p></th></tr></thead>
        <tbody>
          <tr><td><p id="label-1">巡检图片</p></td><td><p id="anchor-b"></p></td><td><p id="anchor-c"></p></td></tr>
          <tr><td id="a3"><p>巡检结果</p></td><td id="b3"><p>合格</p></td><td id="c3"><p>不合格</p></td></tr>
        </tbody>
      </table>
      <table id="table-2">
        <thead><tr><th id="a4"><p>巡检项</p></th><th id="b4"><p>前厅</p></th></tr></thead>
        <tbody>
          <tr><td><p id="label-2">巡检图片</p></td><td><p id="anchor-d"></p></td></tr>
          <tr><td id="a6"><p>巡检结果</p></td><td id="b6"><p>合格</p></td></tr>
        </tbody>
      </table>
    </document>
    """

    assert parse_image_cell_ids(fetched_xml, expected_count=3) == (
        "anchor-b",
        "anchor-c",
        "anchor-d",
    )


def test_parse_image_cell_ids_rejects_incomplete_mapping() -> None:
    fetched_xml = """
    <document><table><tbody>
      <tr><td><p>巡检图片</p></td><td><p id="anchor-b"></p></td></tr>
    </tbody></table></document>
    """

    with pytest.raises(ValueError, match="expected 2 image cells, found 1"):
        parse_image_cell_ids(fetched_xml, expected_count=2)


def test_render_report_document_rejects_empty_images() -> None:
    with pytest.raises(ValueError, match="at least one image"):
        render_report_document((), token_usage=complete_usage())
