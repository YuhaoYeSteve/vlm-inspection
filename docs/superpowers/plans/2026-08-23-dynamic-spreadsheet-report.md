# Dynamic Spreadsheet Inspection Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Docx report exporter with a resumable native Feishu spreadsheet whose rows contain the current task's grouped department headers, images, and matching inspection results.

**Architecture:** A pure `report_sheet.py` renderer converts immutable task items into values, styles, merge ranges, and stable image placements. `ReportBuilder` creates one native workbook, stores its spreadsheet token and worksheet ID atomically, then uploads cell images with bounded concurrency and per-image ledger keys. `LarkCliFeishuClient` is the only external adapter and uses `sheets +workbook-create`, `sheets +workbook-info`, and `sheets +cells-set-image`; the existing report API remains unchanged.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, asyncio, lark-cli Sheets shortcuts, pytest, React 19, TypeScript, Vitest.

---

## File map

- Create `backend/app/services/report_sheet.py`: pure task-to-spreadsheet layout renderer.
- Create `backend/tests/services/test_report_sheet.py`: unit coverage for grouping, text, styles, image coordinates, banding, and fallback names.
- Modify `backend/app/services/feishu_client.py`: expose spreadsheet-oriented protocol and reference type while leaving the legacy HTTP helper isolated.
- Modify `backend/app/services/lark_cli.py`: required scopes plus workbook creation, metadata fallback, and cell-image commands.
- Modify `backend/tests/services/test_lark_cli.py`: exact argv/stdin/cwd parsing and failure tests for the spreadsheet adapter.
- Modify `backend/app/repositories.py`: atomically persist spreadsheet token, URL, worksheet ID, and layout ledger entry.
- Modify `backend/tests/persistence/test_tasks.py`: persistence test for the atomic spreadsheet reference helper.
- Modify `backend/app/services/reports.py`: replace Docx block/XML export with sheet layout and concurrent cell-image publishing.
- Modify `backend/tests/services/test_reports.py`: retry, concurrency, result text, and no-duplicate workbook/image tests.
- Modify `backend/tests/conftest.py`: sheet-capable report fake used by API tests.
- Modify `backend/tests/api/test_reports.py`: retain API contract and assert report regeneration does not invoke inference.
- Delete `backend/app/services/report_matrix.py` and `backend/tests/services/test_report_matrix.py`: remove the superseded Docx-only renderer.
- Modify `frontend/src/features/settings/SettingsPage.tsx`, `frontend/src/features/settings/SettingsPage.test.tsx`, `frontend/src/features/tasks/TaskToolbar.tsx`, `frontend/src/features/tasks/TaskPage.test.tsx`, `frontend/src/features/history/HistoryPage.tsx`, `frontend/src/features/history/HistoryPage.test.tsx`, and `frontend/e2e/inspection-flow.spec.ts`: call the artifact an electronic spreadsheet instead of a document.

### Task 1: Build the pure spreadsheet renderer

**Files:**
- Create: `backend/app/services/report_sheet.py`
- Create: `backend/tests/services/test_report_sheet.py`
- Delete after green: `backend/app/services/report_matrix.py`
- Delete after green: `backend/tests/services/test_report_matrix.py`

- [ ] **Step 1: Write failing renderer tests**

Create tests using the public data objects and assert exact values, merges, sizes, and image coordinates:

```python
from pathlib import Path

from app.services.report_sheet import SheetItem, render_report_sheet


def item(index: int, filename: str, verdict: str, summary: str = "") -> SheetItem:
    return SheetItem(
        image_id=f"image-{index}",
        filename=filename,
        image_path=Path(f"/images/{index}.jpg"),
        verdict=verdict,
        summary=summary,
        unverifiable_reason="被遮挡" if verdict == "unverifiable" else None,
        execution_status="request_failed" if verdict == "failed" else "succeeded",
    )


def test_render_report_sheet_groups_departments_and_maps_images_and_results() -> None:
    layout = render_report_sheet(
        (
            item(1, "行02_D列_安保_01.png", "pass"),
            item(2, "行02_E列_安保_02.png", "fail", "未佩戴工牌"),
            item(3, "行02_F列_工程人员_01.png", "unverifiable", "画面模糊"),
            item(4, "upload.png", "failed"),
        )
    )

    assert layout.values == (
        ("巡检项", "安保", "", "工程人员", "巡检图片"),
        ("巡检图片", "", "", "", ""),
        ("巡检结果", "合格", "不合格\n未佩戴工牌", "无法判断\n被遮挡", "执行失败\nrequest_failed"),
    )
    assert layout.styles["styles"][0]["cell_merges"] == [
        {"range": "B1:C1", "merge_type": "all"}
    ]
    assert [(image.operation_key, image.cell) for image in layout.images] == [
        ("sheet-image:image-1", "B2"),
        ("sheet-image:image-2", "C2"),
        ("sheet-image:image-3", "D2"),
        ("sheet-image:image-4", "E2"),
    ]


def test_render_report_sheet_starts_a_new_three_row_band_after_twelve_images() -> None:
    layout = render_report_sheet(
        tuple(item(i, f"行02_D列_前厅_{i:02}.png", "pass") for i in range(1, 14))
    )

    assert len(layout.values) == 6
    assert layout.values[3][:2] == ("巡检项", "前厅")
    assert layout.images[-1].cell == "B5"
    assert {entry["range"]: entry for entry in layout.styles["styles"][0]["row_sizes"]} == {
        "1:1": {"range": "1:1", "type": "pixel", "size": 38},
        "2:2": {"range": "2:2", "type": "pixel", "size": 124},
        "3:3": {"range": "3:3", "type": "auto"},
        "4:4": {"range": "4:4", "type": "pixel", "size": 38},
        "5:5": {"range": "5:5", "type": "pixel", "size": 124},
        "6:6": {"range": "6:6", "type": "auto"},
    }
```

- [ ] **Step 2: Run the focused tests and verify the missing module failure**

Run: `cd backend && pytest tests/services/test_report_sheet.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: app.services.report_sheet`.

- [ ] **Step 3: Implement the renderer**

Create immutable `SheetItem`, `SheetImagePlacement`, and `SpreadsheetLayout` dataclasses. Parse department names with the existing filename regex, use a one-based fallback sequence without exposing source coordinates, split into 12-image bands, and produce the following style contract:

```python
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
```

Use `_column_name(index)` for A1 coordinates, merge only adjacent same-department spans with length greater than one, set `A` to 110 px, image columns to 105 px, header/image/result rows to 38/124/auto, center all cells, wrap results, fill and bold the header row plus row-label cells, apply thin borders to the full used band, and apply verdict font colors per result cell.

- [ ] **Step 4: Run renderer tests and remove the old renderer**

Run: `cd backend && pytest tests/services/test_report_sheet.py -q`

Expected: PASS. Then remove `report_matrix.py` and its tests; `rg "report_matrix|render_result_matrix|detail_heading" backend` should only show the still-to-be-updated `reports.py` import until Task 4.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/report_sheet.py backend/tests/services/test_report_sheet.py backend/app/services/report_matrix.py backend/tests/services/test_report_matrix.py
git commit -m "feat: render dynamic spreadsheet reports"
```

### Task 2: Add the native spreadsheet CLI adapter and scopes

**Files:**
- Modify: `backend/app/services/feishu_client.py`
- Modify: `backend/app/services/lark_cli.py`
- Modify: `backend/tests/services/test_lark_cli.py`

- [ ] **Step 1: Write failing CLI adapter tests**

Add tests that assert the exact public behavior:

```python
@pytest.mark.asyncio
async def test_report_client_creates_spreadsheet_with_values_styles_and_sheet_id() -> None:
    runner = QueueRunner([{
        "ok": True,
        "identity": "user",
        "data": {
            "spreadsheet": {
                "spreadsheet_token": "sheet-token",
                "url": "https://example.feishu.cn/sheets/sheet-token",
            },
            "sheets": [{"sheet_id": "sheet-1", "title": "Sheet1"}],
        },
    }])
    client = LarkCliFeishuClient(runner)

    ref = await client.create_spreadsheet(
        "巡检报告",
        "folder-1",
        [["巡检项", "安保"]],
        {"styles": [{"name": "Sheet1", "cell_styles": [{"range": "A1:B1"}]}]},
    )

    assert ref == SpreadsheetRef(
        "sheet-token", "https://example.feishu.cn/sheets/sheet-token", "sheet-1"
    )
    assert runner.calls[0]["args"][:3] == ["sheets", "+workbook-create", "--title"]
    assert runner.calls[0]["stdin"] == b'[["巡检项", "安保"]]'
    assert "--values" in runner.calls[0]["args"]
    assert runner.calls[0]["args"][runner.calls[0]["args"].index("--values") + 1] == "-"


@pytest.mark.asyncio
async def test_report_client_uploads_cell_image_from_relative_cwd() -> None:
    runner = QueueRunner([{"ok": True, "identity": "user", "data": {"updated_cells_count": 1}}])
    client = LarkCliFeishuClient(runner)
    path = Path("/tmp/images/001.jpg")

    await client.set_cell_image("sheet-token", "sheet-1", "C2", path, "原图.jpg")

    call = runner.calls[0]
    assert call["cwd"] == path.parent
    assert call["args"] == [
        "sheets", "+cells-set-image",
        "--spreadsheet-token", "sheet-token",
        "--sheet-id", "sheet-1",
        "--range", "C2",
        "--image", "001.jpg",
        "--name", "原图.jpg",
        "--as", "user",
        "--format", "json",
    ]
```

Also cover a create response without `sheets[]`: it must issue `+workbook-info`, select the first grid sheet, and reject envelopes without a token, URL, or worksheet ID with `lark_cli_invalid_response`. Update scope tests so `connected=True` requires `sheets:spreadsheet:create`, `sheets:spreadsheet:write_only`, `sheets:spreadsheet:read`, and `drive:file:upload`.

- [ ] **Step 2: Run the focused CLI tests and verify failure**

Run: `cd backend && pytest tests/services/test_lark_cli.py -q`

Expected: FAIL because `SpreadsheetRef`, `create_spreadsheet`, and `set_cell_image` do not exist and the old Docx scopes are still required.

- [ ] **Step 3: Implement protocol and CLI commands**

Use this public protocol in `feishu_client.py`:

```python
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
```

Set `REQUIRED_REPORT_SCOPES` to:

```python
frozenset({
    "sheets:spreadsheet:create",
    "sheets:spreadsheet:write_only",
    "sheets:spreadsheet:read",
    "drive:file:upload",
})
```

Call workbook creation with `--values -` and UTF-8 JSON on stdin, put the compact styles JSON in `--styles`, always pass `--as user --format json`, and use a 90-second timeout. Parse `data.spreadsheet.spreadsheet_token` (falling back to `data.spreadsheet_token`/`data.token`), derive the URL only when the response omits it, and call `+workbook-info` only if the create response does not include a first grid worksheet ID. For images, pass only `image_path.name` and set `cwd=image_path.parent` so paths with spaces remain safe.

- [ ] **Step 4: Run adapter tests**

Run: `cd backend && pytest tests/services/test_lark_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/feishu_client.py backend/app/services/lark_cli.py backend/tests/services/test_lark_cli.py
git commit -m "feat: publish reports through native Feishu sheets"
```

### Task 3: Persist workbook recovery metadata atomically

**Files:**
- Modify: `backend/app/repositories.py`
- Modify: `backend/tests/persistence/test_tasks.py`

- [ ] **Step 1: Write the failing persistence test**

```python
def test_save_export_spreadsheet_persists_layout_checkpoint(session, completed_task) -> None:
    repo = TaskRepository(session)
    export = repo.get_or_create_active_export(completed_task.id)

    saved = repo.save_export_spreadsheet(
        export.id,
        "spreadsheet-token",
        "https://example.feishu.cn/sheets/spreadsheet-token",
        "worksheet-id",
    )
    session.commit()
    session.expire_all()

    reloaded = repo.get_export(saved.id)
    assert reloaded.document_id == "spreadsheet-token"
    assert reloaded.document_url.endswith("/sheets/spreadsheet-token")
    assert reloaded.remote_blocks_json[REPORT_WORKSHEET_ID_KEY] == "worksheet-id"
    assert REPORT_LAYOUT_OPERATION in reloaded.completed_operations_json
    assert reloaded.cursor == 1
```

- [ ] **Step 2: Run the focused persistence test and verify failure**

Run: `cd backend && pytest tests/persistence/test_tasks.py -q`

Expected: FAIL because the constants and atomic helper are absent.

- [ ] **Step 3: Implement the atomic helper**

Add reserved constants and one repository method:

```python
REPORT_WORKSHEET_ID_KEY = "__report_worksheet_id__"
REPORT_LAYOUT_OPERATION = "sheet:layout"

def save_export_spreadsheet(
    self,
    export_id: str,
    spreadsheet_token: str,
    spreadsheet_url: str,
    worksheet_id: str,
) -> ReportExport:
    export = self.get_export(export_id)
    export.document_id = spreadsheet_token
    export.document_url = spreadsheet_url
    export.remote_blocks_json = {
        **export.remote_blocks_json,
        REPORT_WORKSHEET_ID_KEY: worksheet_id,
    }
    if REPORT_LAYOUT_OPERATION not in export.completed_operations_json:
        export.completed_operations_json = [
            *export.completed_operations_json,
            REPORT_LAYOUT_OPERATION,
        ]
        export.cursor += 1
    return export
```

The builder will call this method and `commit()` in the same session, so the token, URL, worksheet ID, and layout checkpoint cannot diverge in local persistence.

- [ ] **Step 4: Run the persistence tests**

Run: `cd backend && pytest tests/persistence/test_tasks.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories.py backend/tests/persistence/test_tasks.py
git commit -m "feat: persist spreadsheet export checkpoints"
```

### Task 4: Replace the Docx export workflow

**Files:**
- Modify: `backend/app/services/reports.py`
- Modify: `backend/tests/services/test_reports.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/api/test_reports.py`

- [ ] **Step 1: Replace report fakes and write failing workflow tests**

Give the fake client `create_spreadsheet` and `set_cell_image` methods that record workbook payloads, active upload counts, completed cell coordinates, and injected failures. Assert:

```python
@pytest.mark.asyncio
async def test_report_creates_one_spreadsheet_and_places_matching_images(report_fixture) -> None:
    exported = await report_fixture.builder.export(report_fixture.task_id)

    assert exported.status == "succeeded"
    assert exported.document_id == "spreadsheet-1"
    assert exported.document_url == "https://example.feishu.cn/sheets/spreadsheet-1"
    assert report_fixture.feishu.created_spreadsheets == 1
    assert [call.cell for call in report_fixture.feishu.image_calls] == ["B2", "C2", "D2"]
    assert report_fixture.feishu.values[2] == [
        "巡检结果", "合格", "不合格\nresult-2", "无法判断\nimage is obscured"
    ]


@pytest.mark.asyncio
async def test_retry_reuses_workbook_and_skips_completed_cell_images(report_fixture) -> None:
    report_fixture.feishu.fail_cells.add("C2")
    first = await report_fixture.builder.export(report_fixture.task_id)
    report_fixture.feishu.fail_cells.clear()
    second = await report_fixture.builder.export(report_fixture.task_id)

    assert first.status == "failed"
    assert second.status == "succeeded"
    assert report_fixture.feishu.created_spreadsheets == 1
    assert report_fixture.feishu.cell_attempts["B2"] == 1
    assert report_fixture.feishu.cell_attempts["C2"] == 2
    assert report_fixture.feishu.cell_attempts["D2"] == 1


@pytest.mark.asyncio
async def test_cell_image_uploads_use_bounded_concurrency(report_fixture) -> None:
    report_fixture.feishu.delay = 0.01
    await report_fixture.builder.export(report_fixture.task_id)
    assert 1 < report_fixture.feishu.max_active_uploads <= 4
```

Retain tests for new-version behavior and interrupted-export recovery. In the API test, capture `app_harness.inspector.call_count` after inspection, request the report, wait for success, and assert the count is unchanged.

- [ ] **Step 2: Run focused report tests and verify failure**

Run: `cd backend && pytest tests/services/test_reports.py tests/api/test_reports.py -q`

Expected: FAIL because `ReportBuilder` still calls Docx methods.

- [ ] **Step 3: Implement workbook creation and resumable uploads**

Map each `ReportItem` to the renderer without invoking inference:

```python
def _sheet_item(item: ReportItem) -> SheetItem:
    result = item.result
    return SheetItem(
        image_id=item.image_id,
        filename=item.filename,
        image_path=Path(item.thumbnail_path),
        verdict=result.verdict.value if result is not None else "failed",
        summary=result.summary if result is not None else "",
        unverifiable_reason=result.unverifiable_reason if result is not None else None,
        execution_status=item.execution_status,
    )
```

Replace `_write_export` with this sequence:

```python
layout = render_report_sheet(tuple(_sheet_item(item) for item in projection.items))
if export.document_id is None:
    ref = await self.feishu.create_spreadsheet(
        title,
        self._folder_token(),
        [list(row) for row in layout.values],
        layout.styles,
    )
    self._save_spreadsheet(export.id, ref.id, ref.url, ref.sheet_id)
    export = self._get_export(export.id)
sheet_id = self._worksheet_id(export)
await self._write_sheet_images(export, sheet_id, layout.images)
self._mark_succeeded(export.id)
```

Use `asyncio.Semaphore(4)` around external uploads and a separate `asyncio.Lock()` around ledger commits. For every placement, check `sheet-image:{image_id}` before upload, call `set_cell_image(token, sheet_id, cell, path, filename)`, then commit the operation key while holding the ledger lock. Use `asyncio.gather` so multiple independent image cells upload concurrently. A failed upload must leave only that operation incomplete and surface through the existing generic `report_export_failed` path.

Remove all overview, XML matrix, detail block, image block, trace block, and remote Docx block methods. Keep projection/count construction because task APIs still expose it.

- [ ] **Step 4: Run focused report and API tests**

Run: `cd backend && pytest tests/services/test_reports.py tests/api/test_reports.py -q`

Expected: PASS, including one workbook on retry, skipped completed image cells, separate workbook for a new version, and unchanged inference call count.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reports.py backend/tests/services/test_reports.py backend/tests/conftest.py backend/tests/api/test_reports.py
git commit -m "feat: export inspection reports as spreadsheets"
```

### Task 5: Update user-facing spreadsheet copy

**Files:**
- Modify: `frontend/src/features/settings/SettingsPage.tsx`
- Modify: `frontend/src/features/settings/SettingsPage.test.tsx`
- Modify: `frontend/src/features/tasks/TaskToolbar.tsx`
- Modify: `frontend/src/features/tasks/TaskPage.test.tsx`
- Modify: `frontend/src/features/history/HistoryPage.tsx`
- Modify: `frontend/src/features/history/HistoryPage.test.tsx`
- Modify: `frontend/e2e/inspection-flow.spec.ts`

- [ ] **Step 1: Update failing UI expectations first**

Change test expectations to `使用你的身份创建电子表格并回填巡检图片。`, `打开参考表格`, `打开飞书表格`, and accessible history labels such as `打开早班巡检飞书表格`.

- [ ] **Step 2: Run focused UI tests and verify old-copy failures**

Run: `cd frontend && npm test -- --run src/features/settings/SettingsPage.test.tsx src/features/tasks/TaskPage.test.tsx src/features/history/HistoryPage.test.tsx`

Expected: FAIL because the rendered UI still says `文档`.

- [ ] **Step 3: Change the UI copy**

In settings, say `使用你的身份创建电子表格并回填巡检图片。` and rename the reference link to `打开参考表格`. In task and history report links, replace `飞书文档`/`文档` with `飞书表格`/`表格`. Do not change API fields such as `report_url`.

- [ ] **Step 4: Run frontend unit tests and build**

Run: `cd frontend && npm test && npm run build`

Expected: 17 tests pass and the production build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src frontend/e2e/inspection-flow.spec.ts
git commit -m "fix: describe report artifacts as spreadsheets"
```

### Task 6: Full verification and live regeneration

**Files:**
- Modify only if verification exposes a defect in files already listed above.
- Preserve: untracked `feishu-*-auth-qr.png` files.

- [ ] **Step 1: Run all automated verification**

Run:

```bash
cd backend && pytest -q
cd backend && ruff check app tests
cd backend && mypy app tests
cd frontend && npm test
cd frontend && npm run build
```

Expected: all commands pass; the known Python 3.12 asyncio teardown warning may still appear without failing tests.

- [ ] **Step 2: Inspect the final diff**

Run: `git diff --check && git status --short && rg "append_document_xml|render_result_matrix|打开飞书文档|使用你的身份创建文档" backend frontend`

Expected: no whitespace errors, only intentional files changed/untracked QR images present, and no runtime references to the superseded Docx report path or copy.

- [ ] **Step 3: Request fresh minimal user authorization only when needed**

If status lacks create/write scopes, run exactly:

```bash
lark-cli auth login --scope "sheets:spreadsheet:create sheets:spreadsheet:write_only" --no-wait --json
```

Generate a new PNG from the returned opaque URL with `lark-cli auth qrcode '<exact verification_url>' --output feishu-sheets-write-auth-qr.png`, show both the exact URL and image to the user, end the turn, then after the user confirms run `lark-cli auth login --device-code <fresh device_code>` personally. Never reuse an expired device code.

- [ ] **Step 4: Restart the app with the corrected code and regenerate**

Stop the old port-8000 server, start this worktree's backend, and request a new report version for task `25e7e2d6-70ff-4388-8957-66ab2beb453a`. Record the inference-attempt count before and after; both must remain `12`.

- [ ] **Step 5: Read the live spreadsheet back**

Use `lark-cli sheets +workbook-info --url '<new report_url>' --as user --format json`, then `+csv-get` for the exact used range and `+sheet-info --include merges,row_heights,column_widths` for layout. Verify one checklist item at a time:

1. The artifact is a native spreadsheet in the configured folder.
2. Header spans are `安保 × 2`, `工程人员 × 1`, `客房 × 3`, `前厅 × 6`.
3. All 12 first-band image cells contain embedded images.
4. All 12 result cells are directly below their images.
5. Both failing cells include their stored summaries.
6. No reference-sheet values, URLs, or coordinates appear.
7. Inference-attempt count remains 12.

- [ ] **Step 6: Commit verification fixes, if any, and report the live URL**

Run `git status --short --branch` and `git log -6 --oneline`. If verification required code changes, commit those exact files with `fix: verify dynamic spreadsheet reports`; otherwise do not create an empty commit. Return the new spreadsheet URL and the automated/live verification results.
