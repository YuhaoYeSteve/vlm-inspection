# Grouped Inspection Report Matrix Implementation Plan

> Superseded by the approved native-spreadsheet design in `../specs/2026-08-23-dynamic-spreadsheet-report-design.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a native two-row Feishu Docx matrix with merged department headers and per-image verdicts, then generate a new version of the existing report without rerunning inference.

**Architecture:** A new pure renderer derives display groups from immutable upload filenames and emits escaped Docx XML in batches of at most 12 result columns. The report builder appends that XML through one new Feishu client operation between the overview and detail blocks, while the existing export-operation ledger preserves retry behavior.

**Tech Stack:** Python 3.12, dataclasses, regular expressions, XML escaping, FastAPI service layer, pytest, `lark-cli docs +update`, Feishu Docx XML.

---

## File map

- Create `backend/app/services/report_matrix.py` — parse display metadata, group adjacent departments, translate verdicts, and render native Docx XML.
- Create `backend/tests/services/test_report_matrix.py` — unit coverage for merged headers, second-row verdicts, fallback labels, batching, and escaping.
- Modify `backend/app/services/feishu_client.py` — extend the report client protocol and legacy adapter with XML append support.
- Modify `backend/app/services/lark_cli.py` — append XML through `lark-cli docs +update` using stdin and user identity.
- Modify `backend/app/services/reports.py` — insert the matrix between overview and details and use readable detail headings.
- Modify `backend/tests/services/test_lark_cli.py` — verify the CLI command contract and errors.
- Modify `backend/tests/services/test_reports.py` — verify operation order and retry idempotency.
- Modify `backend/tests/conftest.py` and `backend/tests/e2e_server.py` — keep report-client test doubles aligned with the protocol.

## Task 1: Build the pure grouped-matrix renderer

**Files:**
- Create: `backend/app/services/report_matrix.py`
- Create: `backend/tests/services/test_report_matrix.py`

- [ ] **Step 1: Write failing renderer tests**

Create `backend/tests/services/test_report_matrix.py`:

```python
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
    verdicts = ("pass", "pass", "pass", "pass", "pass", "fail", "pass", "pass", "pass", "pass", "fail", "pass")
    return tuple(MatrixItem(name, verdict) for name, verdict in zip(names, verdicts, strict=True))


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
```

- [ ] **Step 2: Run the renderer tests and verify RED**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_report_matrix.py -v
```

Expected: collection fails because `app.services.report_matrix` does not exist.

- [ ] **Step 3: Implement the renderer**

Create `backend/app/services/report_matrix.py` with these contracts:

```python
import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


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
        f'{html.escape(name)}</th>'
        for name, size in _groups(cells)
    )
    results = "".join(
        '<td vertical-align="middle"><p align="center">'
        f'{html.escape(cell.sequence)}<br/><b><span text-color="{cell.verdict_color}">'
        f'{cell.verdict_label}</span></b></p></td>'
        for cell in cells
    )
    return (
        "<table><colgroup><col width=\"96\"/>"
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
```

- [ ] **Step 4: Run renderer tests and verify GREEN**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_report_matrix.py -v
backend/.venv/bin/ruff check backend/app/services/report_matrix.py backend/tests/services/test_report_matrix.py
```

Expected: all renderer tests pass and Ruff reports no errors.

- [ ] **Step 5: Commit the pure renderer**

```bash
git add backend/app/services/report_matrix.py backend/tests/services/test_report_matrix.py
git commit -m "feat: render grouped inspection result matrix"
```

## Task 2: Add one-shot Docx XML append support

**Files:**
- Modify: `backend/app/services/feishu_client.py`
- Modify: `backend/app/services/lark_cli.py`
- Modify: `backend/tests/services/test_lark_cli.py`
- Modify: `backend/tests/services/test_reports.py`

- [ ] **Step 1: Write the failing CLI contract test**

Add to `backend/tests/services/test_lark_cli.py`:

```python
@pytest.mark.asyncio
async def test_report_client_appends_docx_xml_through_stdin() -> None:
    runner = QueueRunner(
        [{"ok": True, "identity": "user", "data": {"result": "success"}}]
    )
    client = LarkCliFeishuClient(runner)

    await client.append_document_xml("doc-1", "<table></table>", "matrix:1")

    call = runner.calls[0]
    assert call["args"] == [
        "docs", "+update", "--doc", "doc-1", "--command", "append",
        "--doc-format", "xml", "--content", "-", "--as", "user", "--format", "json",
    ]
    assert call["stdin"] == b"<table></table>"


@pytest.mark.asyncio
async def test_report_client_rejects_unsuccessful_docx_xml_update() -> None:
    runner = QueueRunner(
        [{"ok": True, "identity": "user", "data": {"result": "failed"}}]
    )
    client = LarkCliFeishuClient(runner)

    with pytest.raises(LarkCliCommandError) as raised:
        await client.append_document_xml("doc-1", "<table></table>", "matrix:1")

    assert raised.value.code == "lark_cli_invalid_response"
```

Add this test beside the existing direct `FeishuClient` tests in
`backend/tests/services/test_reports.py`:

```python
@pytest.mark.asyncio
async def test_feishu_client_appends_document_xml() -> None:
    oauth = FakeOAuth()
    http = FakeRequestHttp(
        [api_response(200, {"code": 0, "data": {"result": "success"}})]
    )
    client = FeishuClient(oauth, http=http, sleep=lambda _seconds: asyncio.sleep(0))

    await client.append_document_xml("doc-1", "<table></table>", "matrix:1")

    request = http.requests[0]
    assert request["method"] == "PUT"
    assert request["url"].endswith("/open-apis/docs_ai/v1/documents/doc-1")
    assert request["json"] == {
        "block_id": "-1",
        "command": "block_insert_after",
        "content": "<table></table>",
        "format": "xml",
        "revision_id": -1,
    }
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
backend/.venv/bin/pytest \
  backend/tests/services/test_lark_cli.py::test_report_client_appends_docx_xml_through_stdin \
  backend/tests/services/test_lark_cli.py::test_report_client_rejects_unsuccessful_docx_xml_update \
  backend/tests/services/test_reports.py::test_feishu_client_appends_document_xml -v
```

Expected: failure because `append_document_xml` is missing.

- [ ] **Step 3: Extend the protocol and adapters**

Add this method to `FeishuClientProtocol`:

```python
async def append_document_xml(
    self,
    document_id: str,
    content: str,
    operation_key: str,
) -> None: ...
```

Implement the method in `LarkCliFeishuClient`:

```python
async def append_document_xml(
    self,
    document_id: str,
    content: str,
    operation_key: str,
) -> None:
    del operation_key
    payload = await self.runner.run(
        [
            "docs", "+update", "--doc", document_id,
            "--command", "append", "--doc-format", "xml",
            "--content", "-", "--as", "user", "--format", "json",
        ],
        stdin=content.encode("utf-8"),
        timeout=60,
    )
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("result") != "success":
        raise LarkCliCommandError("lark_cli_invalid_response")
```

Implement the equivalent fallback in `FeishuClient` through its existing authenticated request path:

```python
async def append_document_xml(
    self,
    document_id: str,
    content: str,
    operation_key: str,
) -> None:
    del operation_key
    await self._document_rate_limit.wait()
    payload = await self._request(
        "PUT",
        f"/open-apis/docs_ai/v1/documents/{document_id}",
        json={
            "block_id": "-1",
            "command": "block_insert_after",
            "content": content,
            "format": "xml",
            "revision_id": -1,
        },
    )
    if payload.get("data", {}).get("result") != "success":
        raise FeishuApiError("document_update_failed")
```

- [ ] **Step 4: Run adapter tests and verify GREEN**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_lark_cli.py -v
backend/.venv/bin/pytest backend/tests/services/test_reports.py::test_feishu_client_appends_document_xml -v
backend/.venv/bin/ruff check backend/app/services/feishu_client.py backend/app/services/lark_cli.py backend/tests/services/test_lark_cli.py
backend/.venv/bin/mypy backend/app
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit the adapter**

```bash
git add backend/app/services/feishu_client.py backend/app/services/lark_cli.py backend/tests/services/test_lark_cli.py backend/tests/services/test_reports.py
git commit -m "feat: append native tables to Feishu reports"
```

## Task 3: Insert the matrix into report exports

**Files:**
- Modify: `backend/app/services/reports.py`
- Modify: `backend/tests/services/test_reports.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/e2e_server.py`

- [ ] **Step 1: Write failing report integration tests**

In `FakeFeishuClient.__init__`, add:

```python
self.xml_payloads: list[str] = []
```

Then add this method to `FakeFeishuClient`:

```python
async def append_document_xml(
    self,
    document_id: str,
    content: str,
    operation_key: str,
) -> None:
    del document_id
    self.xml_payloads.append(content)
    self._operation("xml", operation_key)
```

Replace the fixture's current verdict-only loop with source-shaped filenames:

```python
        filenames = (
            "行02_D列_安保_01.png",
            "行02_E列_安保_02.png",
            "行02_F列_前厅_01.png",
        )
        verdicts = (Verdict.PASS, Verdict.FAIL, Verdict.UNVERIFIABLE)
        for position, (filename, verdict) in enumerate(
            zip(filenames, verdicts, strict=True),
            start=1,
        ):
            thumbnail = tmp_path / f"{position:03}.jpg"
            thumbnail.write_bytes(f"thumbnail-{position}".encode())
            image = repo.add_image(
                task.id,
                position - 1,
                filename,
                f"hash-{position}",
                str(thumbnail),
                str(thumbnail),
                str(thumbnail),
            )
```

Replace the projection filename assertion with:

```python
assert [item.filename for item in projection.items] == [
    "行02_D列_安保_01.png",
    "行02_E列_安保_02.png",
    "行02_F列_前厅_01.png",
]
```

Then add:

```python
@pytest.mark.asyncio
async def test_report_places_grouped_matrix_before_details(report_fixture) -> None:
    await report_fixture.builder.export(report_fixture.task_id)

    assert len(report_fixture.feishu.xml_payloads) == 1
    matrix = report_fixture.feishu.xml_payloads[0]
    assert "巡检结果总览" in matrix
    assert "安保" in matrix
    assert "合格" in matrix and "不合格" in matrix and "无法判断" in matrix
    assert report_fixture.feishu.calls[1] == ("blocks", "overview")
    assert report_fixture.feishu.calls[2] == ("xml", "matrix")


@pytest.mark.asyncio
async def test_report_retry_does_not_append_matrix_twice(report_fixture) -> None:
    report_fixture.feishu.fail_after_operations = 5
    first = await report_fixture.builder.export(report_fixture.task_id)
    report_fixture.feishu.fail_after_operations = None
    second = await report_fixture.builder.export(report_fixture.task_id)

    assert first.status == "failed"
    assert second.status == "succeeded"
    assert [call for call in report_fixture.feishu.calls if call == ("xml", "matrix")] == [
        ("xml", "matrix")
    ]
```

Add this exact method to `FakeReportFeishuClient` in `backend/tests/conftest.py`:

```python
    async def append_document_xml(
        self,
        document_id: str,
        content: str,
        operation_key: str,
    ) -> None:
        del document_id, content
        self.calls.append(operation_key)
```

Add this exact method to `DeterministicFeishuClient` in
`backend/tests/e2e_server.py`:

```python
    async def append_document_xml(
        self,
        document_id: str,
        content: str,
        operation_key: str,
    ) -> None:
        del document_id, content, operation_key
```

- [ ] **Step 2: Run report tests and verify RED**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_reports.py -k 'matrix or retry' -v
```

Expected: matrix assertions fail because the report builder never appends XML.

- [ ] **Step 3: Integrate the renderer and operation ledger**

Add the immutable zero-based image position to `ReportItem`:

```python
@dataclass(frozen=True)
class ReportItem:
    image_id: str
    position: int
    filename: str
    thumbnail_path: str
    execution_status: str
    result: InspectionResult | None
```

Populate it in `ReportBuilder.project`:

```python
                    ReportItem(
                        image_id=image.id,
                        position=image.position,
                        filename=image.original_name,
                        thumbnail_path=image.thumbnail_path,
                        execution_status=image.status,
                        result=parsed,
                    )
```

In `ReportBuilder._write_export`, insert the matrix after `overview`:

```python
        await self._write_blocks(export, "overview", self._overview_blocks(projection))
        matrix_items = tuple(
            MatrixItem(
                item.filename,
                item.result.verdict.value if item.result is not None else "failed",
            )
            for item in projection.items
        )
        await self._write_xml(
            export,
            "matrix",
            render_result_matrix(matrix_items),
        )
```

Add the resumable helper:

```python
    async def _write_xml(
        self,
        export: ReportExport,
        operation_key: str,
        content: str,
    ) -> None:
        if self._operation_completed(export.id, operation_key):
            return
        assert export.document_id is not None
        await self.feishu.append_document_xml(
            export.document_id,
            content,
            operation_key,
        )
        self._mark_operation_completed(export.id, operation_key)
```

Replace the first line of `_item_blocks` with:

```python
verdict = item.result.verdict.value if item.result is not None else "failed"
blocks = [self._text_block(detail_heading(item.filename, verdict, item.position), heading=True)]
```

- [ ] **Step 4: Run report tests and verify GREEN**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_report_matrix.py backend/tests/services/test_reports.py backend/tests/api/test_reports.py -v
backend/.venv/bin/ruff check backend/app backend/tests
backend/.venv/bin/mypy backend/app
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit report integration**

```bash
git add backend/app/services/reports.py backend/tests/services/test_reports.py backend/tests/conftest.py backend/tests/e2e_server.py
git commit -m "feat: add grouped matrix to inspection reports"
```

## Task 4: Verify and generate the new report version

**Files:**
- No tracked-file changes expected.

- [ ] **Step 1: Run the complete automated verification suite**

Run:

```bash
backend/.venv/bin/ruff check backend/app backend/tests
backend/.venv/bin/mypy backend/app
backend/.venv/bin/pytest backend/tests -v
npm --prefix frontend test
npm --prefix frontend run build
```

Expected: every command exits 0 with no failing tests, lint errors, type errors, or build errors.

- [ ] **Step 2: Restart the local app with the existing ignored `.env`**

Stop only this worktree's `scripts/start.sh` process, then run:

```bash
VLM_NO_OPEN=1 ./scripts/start.sh
```

Confirm the process is healthy and `/api/auth/feishu/status` reports `connected: true`:

```bash
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/auth/feishu/status \
  | backend/.venv/bin/python -c \
    'import json, sys; payload = json.load(sys.stdin); assert payload["connected"] is True; print(payload["user_name"])'
```

- [ ] **Step 3: Record the inference count and request a new report version**

Run:

```bash
sqlite3 "$HOME/.vlm-inspector/app.db" 'select count(*) from inference_attempts;'
curl -sS -X POST \
  http://127.0.0.1:8000/api/tasks/25e7e2d6-70ff-4388-8957-66ab2beb453a/report/new-version
```

Expected: inference count is 12 and the API returns a new export ID with status `accepted`.

- [ ] **Step 4: Poll and verify local completion**

Poll `GET /api/tasks/25e7e2d6-70ff-4388-8957-66ab2beb453a` with:

```bash
backend/.venv/bin/python - <<'PY'
import json
import time
import urllib.request

url = "http://127.0.0.1:8000/api/tasks/25e7e2d6-70ff-4388-8957-66ab2beb453a"
deadline = time.monotonic() + 180
while time.monotonic() < deadline:
    with urllib.request.urlopen(url) as response:
        task = json.load(response)
    if task["report_status"] in {"succeeded", "failed"}:
        break
    time.sleep(1)
else:
    raise SystemExit("report did not finish within 180 seconds")

assert task["status"] == "completed", task
assert task["counts"] == {"pass": 10, "fail": 2, "unverifiable": 0, "failed": 0}, task
assert task["report_status"] == "succeeded", task
print(json.dumps(task, ensure_ascii=False))
PY
```

The final payload must contain:

```json
{
  "status": "completed",
  "counts": {"pass": 10, "fail": 2, "unverifiable": 0, "failed": 0},
  "report_status": "succeeded"
}
```

Then rerun the inference-count query and confirm it is still 12.

- [ ] **Step 5: Fetch and inspect the new Feishu document**

Use the returned URL with:

```bash
REPORT_URL="$(curl -fsS \
  http://127.0.0.1:8000/api/tasks/25e7e2d6-70ff-4388-8957-66ab2beb453a \
  | backend/.venv/bin/python -c \
    'import json, sys; print(json.load(sys.stdin)["report_url"])')"
test -n "$REPORT_URL" && test "$REPORT_URL" != "None"
LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1 \
  lark-cli docs +fetch --doc "$REPORT_URL" --as user --detail simple --format json \
  | backend/.venv/bin/python -c '
import json
import sys
import xml.etree.ElementTree as ET

content = json.load(sys.stdin)["data"]["document"]["content"]
root = ET.fromstring(f"<root>{content}</root>")
headings = ["".join(node.itertext()) for node in root.iter("h1")]
assert headings.count("巡检结果总览") == 1, headings
tables = list(root.iter("table"))
assert len(tables) == 1, len(tables)
rows = list(tables[0].iter("tr"))
assert len(rows) == 2, len(rows)
headers = list(rows[0])
assert headers[0].attrib.get("rowspan") == "2", headers[0].attrib
assert [("".join(cell.itertext()), cell.attrib.get("colspan", "1")) for cell in headers[1:]] == [
    ("安保", "2"), ("工程人员", "1"), ("客房", "3"), ("前厅", "6")
]
result_cells = list(rows[1].findall("td"))
assert len(result_cells) == 12, len(result_cells)
verdicts = ["".join(node.itertext()) for node in rows[1].iter("b")]
assert verdicts.count("合格") == 10, verdicts
assert verdicts.count("不合格") == 2, verdicts
assert len([heading for heading in headings if "｜" in heading]) == 12, headings
assert len(list(root.iter("img"))) == 12
assert "D2" not in content and "O2" not in content
assert "ocn3t6tvv2gx.feishu.cn/wiki/UAJDwztfriDd2fkK1KFcPcbgn7g" not in content
print(sys.argv[1])
' "$REPORT_URL"
```

- [ ] **Step 6: Inspect the final branch state**

Run:

```bash
git status --short
git diff --check main...HEAD
git log --oneline -12
```

Expected: source and plan changes are committed; ignored `.env` remains untracked by Git, and generated authorization QR PNGs remain unstaged.
