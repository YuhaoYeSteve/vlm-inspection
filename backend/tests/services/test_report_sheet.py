from pathlib import Path

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


def test_render_report_sheet_groups_current_task_and_maps_images_to_result_columns() -> None:
    layout = render_report_sheet(current_items())

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
    assert layout.styles["styles"][0]["cell_merges"] == [
        {"range": "B1:C1", "merge_type": "all"},
        {"range": "E1:G1", "merge_type": "all"},
        {"range": "H1:M1", "merge_type": "all"},
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
        )
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
        {"range": "B1:F1", "merge_type": "all"}
    ]


def test_render_report_sheet_uses_reference_proportions_and_visual_styles() -> None:
    layout = render_report_sheet((item(1, "行02_D列_安保_01.png", "pass"),))
    sheet_style = layout.styles["styles"][0]

    assert sheet_style["row_sizes"] == [
        {"range": "1:1", "type": "pixel", "size": 38},
        {"range": "2:2", "type": "pixel", "size": 124},
        {"range": "3:3", "type": "auto"},
    ]
    assert sheet_style["col_sizes"] == [
        {"range": "A:A", "type": "pixel", "size": 110},
        {"range": "B:B", "type": "pixel", "size": 105},
    ]
    assert {
        style["range"]: style
        for style in sheet_style["cell_styles"]
    }["A1:B1"] == {
        "range": "A1:B1",
        "background_color": "#DEE0E3",
        "font_weight": "bold",
        "horizontal_alignment": "center",
        "vertical_alignment": "middle",
        "border": {"style": "solid", "weight": "thin", "color": "#BBBFC4"},
    }


def test_render_report_sheet_starts_a_new_three_row_band_after_twelve_images() -> None:
    layout = render_report_sheet(
        tuple(
            item(index, f"行02_D列_前厅_{index:02}.png", "pass")
            for index in range(1, 14)
        )
    )

    assert len(layout.values) == 6
    assert layout.values[3][:2] == ("巡检项", "前厅")
    assert layout.values[4][:2] == ("巡检图片", "")
    assert layout.values[5][:2] == ("巡检结果", "合格")
    assert layout.images[-1].cell == "B5"
    assert layout.styles["styles"][0]["row_sizes"] == [
        {"range": "1:1", "type": "pixel", "size": 38},
        {"range": "2:2", "type": "pixel", "size": 124},
        {"range": "3:3", "type": "auto"},
        {"range": "4:4", "type": "pixel", "size": 38},
        {"range": "5:5", "type": "pixel", "size": 124},
        {"range": "6:6", "type": "auto"},
    ]
