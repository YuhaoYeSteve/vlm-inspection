from pathlib import Path

import pytest

from app.domain.usage import TokenUsageSummary, UsageAvailability
from app.services.report_sheet import SheetItem, render_report_sheet


def item(
    index: int,
    filename: str,
    verdict: str,
    summary: str = "",
    *,
    unverifiable_reason: str | None = None,
    execution_status: str = "succeeded",
) -> SheetItem:
    return SheetItem(
        image_id=f"image-{index}",
        filename=filename,
        image_path=Path(f"/images/{index}.jpg"),
        verdict=verdict,
        summary=summary,
        unverifiable_reason=unverifiable_reason,
        execution_status=execution_status,
    )


def current_items() -> tuple[SheetItem, ...]:
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


def empty_usage(
    availability: UsageAvailability,
    unknown_call_count: int = 0,
) -> TokenUsageSummary:
    return TokenUsageSummary(
        availability=availability,
        input_tokens=0,
        cached_input_tokens=0,
        uncached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        total_tokens=0,
        known_call_count=0,
        unknown_call_count=unknown_call_count,
    )


def test_render_report_sheet_groups_current_task_and_maps_images_to_result_columns() -> None:
    layout = render_report_sheet(current_items(), token_usage=complete_usage())

    assert layout.values[0] == (
        "巡检项",
        "安保",
        "",
        "工程人员",
        "客房",
        "",
        "",
        "前厅",
        "",
        "",
        "",
        "",
        "",
    )
    assert layout.values[1] == ("巡检图片", *("" for _ in range(12)))
    assert layout.values[2][0] == "巡检结果"
    assert layout.values[2][6] == "不合格\n问题-6"
    assert layout.values[2][11] == "不合格\n问题-11"
    assert layout.values[2].count("合格") == 10
    assert layout.values[3] == ("模型 Token 用量", *("" for _ in range(12)))
    assert layout.values[4][0] == (
        "总 Token：36\n"
        "输入：30\n"
        "缓存命中：12\n"
        "未缓存输入：18\n"
        "输出：6\n"
        "推理 Token：0（包含在输出中）\n"
        "已知调用：3"
    )
    assert layout.values[4][1:] == ("",) * 12
    assert layout.styles["styles"][0]["cell_merges"] == [
        {"range": "B1:C1", "merge_type": "all"},
        {"range": "E1:G1", "merge_type": "all"},
        {"range": "H1:M1", "merge_type": "all"},
        {"range": "A4:M4", "merge_type": "all"},
        {"range": "A5:M5", "merge_type": "all"},
    ]
    assert [placement.cell for placement in layout.images] == [
        "B2",
        "C2",
        "D2",
        "E2",
        "F2",
        "G2",
        "H2",
        "I2",
        "J2",
        "K2",
        "L2",
        "M2",
    ]
    assert [placement.operation_key for placement in layout.images] == [
        f"sheet-image:image-{index}" for index in range(1, 13)
    ]


def test_render_report_sheet_formats_all_result_states_and_fallback_filename() -> None:
    layout = render_report_sheet(
        (
            item(1, "one.png", "pass", "不会显示"),
            item(2, "two.png", "fail", "未佩戴工牌"),
            item(
                3,
                "three.png",
                "unverifiable",
                "画面模糊",
                unverifiable_reason="人物被遮挡",
            ),
            item(4, "four.png", "unverifiable", "画面模糊"),
            item(5, "five.png", "failed", execution_status="request_failed"),
        ),
        token_usage=complete_usage(),
    )

    assert layout.values[0] == (
        "巡检项",
        "巡检图片",
        "",
        "",
        "",
        "",
    )
    assert layout.values[2] == (
        "巡检结果",
        "合格",
        "不合格\n未佩戴工牌",
        "无法判断\n人物被遮挡",
        "无法判断\n画面模糊",
        "执行失败\nrequest_failed",
    )
    assert layout.styles["styles"][0]["cell_merges"] == [
        {"range": "B1:F1", "merge_type": "all"},
        {"range": "A4:F4", "merge_type": "all"},
        {"range": "A5:F5", "merge_type": "all"},
    ]


def test_render_report_sheet_uses_reference_proportions_and_visual_styles() -> None:
    layout = render_report_sheet(
        (item(1, "行02_D列_安保_01.png", "pass"),),
        token_usage=complete_usage(),
    )
    sheet_style = layout.styles["styles"][0]

    assert sheet_style["row_sizes"] == [
        {"range": "1:1", "type": "pixel", "size": 38},
        {"range": "2:2", "type": "pixel", "size": 124},
        {"range": "3:3", "type": "auto"},
        {"range": "4:4", "type": "pixel", "size": 38},
        {"range": "5:5", "type": "auto"},
    ]
    assert sheet_style["col_sizes"] == [
        {"range": "A:A", "type": "pixel", "size": 110},
        {"range": "B:B", "type": "pixel", "size": 105},
    ]
    styles_by_range = {
        style["range"]: style for style in sheet_style["cell_styles"]
    }
    assert styles_by_range["A1:B1"] == {
        "range": "A1:B1",
        "background_color": "#DEE0E3",
        "font_weight": "bold",
        "horizontal_alignment": "center",
        "vertical_alignment": "middle",
        "border": {"style": "solid", "weight": "thin", "color": "#BBBFC4"},
    }
    assert styles_by_range["A4:B4"] == {
        "range": "A4:B4",
        "background_color": "#DEE0E3",
        "font_weight": "bold",
        "horizontal_alignment": "left",
        "vertical_alignment": "middle",
        "border": {"style": "solid", "weight": "thin", "color": "#BBBFC4"},
    }
    assert styles_by_range["A5:B5"] == {
        "range": "A5:B5",
        "horizontal_alignment": "left",
        "vertical_alignment": "top",
        "word_wrap": "auto-wrap",
        "border": {"style": "solid", "weight": "thin", "color": "#BBBFC4"},
    }


def test_render_report_sheet_starts_a_new_three_row_band_after_twelve_images() -> None:
    layout = render_report_sheet(
        tuple(
            item(index, f"行02_D列_前厅_{index:02}.png", "pass")
            for index in range(1, 14)
        ),
        token_usage=complete_usage(),
    )

    assert len(layout.values) == 8
    assert layout.values[3][:2] == ("巡检项", "前厅")
    assert layout.values[4][:2] == ("巡检图片", "")
    assert layout.values[5][:2] == ("巡检结果", "合格")
    assert layout.values[6][0] == "模型 Token 用量"
    assert layout.images[-1].cell == "B5"
    assert layout.styles["styles"][0]["row_sizes"] == [
        {"range": "1:1", "type": "pixel", "size": 38},
        {"range": "2:2", "type": "pixel", "size": 124},
        {"range": "3:3", "type": "auto"},
        {"range": "4:4", "type": "pixel", "size": 38},
        {"range": "5:5", "type": "pixel", "size": 124},
        {"range": "6:6", "type": "auto"},
        {"range": "7:7", "type": "pixel", "size": 38},
        {"range": "8:8", "type": "auto"},
    ]
    assert layout.styles["styles"][0]["cell_merges"][-2:] == [
        {"range": "A7:M7", "merge_type": "all"},
        {"range": "A8:M8", "merge_type": "all"},
    ]


def test_render_report_sheet_formats_partial_usage_and_unknown_disclaimer() -> None:
    token_usage = TokenUsageSummary(
        availability=UsageAvailability.PARTIAL,
        input_tokens=1_000,
        cached_input_tokens=400,
        uncached_input_tokens=600,
        output_tokens=234,
        reasoning_tokens=34,
        total_tokens=1_234,
        known_call_count=1,
        unknown_call_count=2,
    )

    layout = render_report_sheet(
        (item(1, "行02_D列_安保_01.png", "pass"),),
        token_usage=token_usage,
    )

    assert layout.values[-1][0] == (
        "已知总 Token：1,234\n"
        "输入：1,000\n"
        "缓存命中：400\n"
        "未缓存输入：600\n"
        "输出：234\n"
        "推理 Token：34（包含在输出中）\n"
        "已知调用：1\n"
        "未返回、格式异常或未采集 usage：至少 2 次；不计入已知总量。"
    )
    assert "raw_usage" not in layout.values[-1][0]
    assert "费用" not in layout.values[-1][0]


@pytest.mark.parametrize(
    ("token_usage", "expected"),
    [
        pytest.param(
            empty_usage(UsageAvailability.NOT_STARTED),
            "尚无模型调用。",
            id="not-started",
        ),
        pytest.param(
            empty_usage(UsageAvailability.UNTRACKED, unknown_call_count=2),
            "该任务运行时尚未采集 Token 用量。\n未采集 usage：至少 2 次。",
            id="untracked",
        ),
        pytest.param(
            empty_usage(UsageAvailability.UNAVAILABLE, unknown_call_count=2),
            "没有可用的 Token 用量（未返回或格式异常）。\n"
            "未返回或格式异常 usage：至少 2 次。",
            id="unavailable",
        ),
    ],
)
def test_render_report_sheet_distinguishes_unknown_usage_states(
    token_usage: TokenUsageSummary,
    expected: str,
) -> None:
    layout = render_report_sheet(
        (item(1, "行02_D列_安保_01.png", "pass"),),
        token_usage=token_usage,
    )

    assert layout.values[-1][0] == expected
    assert "总 Token：0" not in layout.values[-1][0]
