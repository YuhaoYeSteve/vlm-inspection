from app.services.report_matrix import MatrixItem, detail_heading, render_result_matrix


def current_items() -> tuple[MatrixItem, ...]:
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
    verdicts = (
        "pass",
        "pass",
        "pass",
        "pass",
        "pass",
        "fail",
        "pass",
        "pass",
        "pass",
        "pass",
        "fail",
        "pass",
    )
    return tuple(
        MatrixItem(name, verdict)
        for name, verdict in zip(names, verdicts, strict=True)
    )


def test_matrix_has_merged_department_headers_and_verdicts_on_second_row() -> None:
    xml = render_result_matrix(current_items())

    assert '<th colspan="2"' in xml and ">安保</th>" in xml
    assert '<th colspan="1"' in xml and ">工程人员</th>" in xml
    assert '<th colspan="3"' in xml and ">客房</th>" in xml
    assert '<th colspan="6"' in xml and ">前厅</th>" in xml
    assert '<th rowspan="2"' in xml and ">巡检结果</th>" in xml
    assert xml.count("<td ") == 12
    assert xml.count(">合格</span>") == 10
    assert xml.count(">不合格</span>") == 2
    assert "D2" not in xml and "O2" not in xml


def test_matrix_escapes_labels_and_batches_after_twelve_results() -> None:
    items = tuple(MatrixItem(f"item-{index}.png", "pass") for index in range(13))
    items = (MatrixItem("行02_D列_A&B_01.png", "unverifiable"), *items[1:])

    xml = render_result_matrix(items)

    assert "A&amp;B" in xml
    assert "无法判断" in xml
    assert xml.count("<table>") == 2
    assert xml.count("<td ") == 13


def test_detail_heading_hides_source_coordinates() -> None:
    assert detail_heading("行02_N列_前厅_05.png", "fail", 10) == "前厅 05｜不合格"
    assert detail_heading("front-desk.png", "pass", 2) == "front-desk｜合格"
