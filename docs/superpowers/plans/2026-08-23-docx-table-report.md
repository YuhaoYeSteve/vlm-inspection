# Native Docx Inspection Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate each new inspection report as a native Feishu Docx containing the grouped image/result table instead of as a standalone spreadsheet.

**Architecture:** A pure renderer produces Docx XML and later parses empty paragraph anchors inside image-row cells from a `docs +fetch --detail with-ids` response. The report coordinator persists the document, layout, anchor mapping, image-block IDs, and upload checkpoints so retries reuse remote objects. `LarkCliFeishuClient` combines idempotent OpenAPI document/block creation with `lark-cli docs` XML append/fetch/block movement and Drive media upload.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, lark-cli, Feishu Docx OpenAPI, React, Vitest, pytest.

---

### Task 1: Render and parse the native Docx report table

**Files:**
- Create: `backend/app/services/report_document.py`
- Create: `backend/tests/services/test_report_document.py`
- Delete after migration: `backend/app/services/report_sheet.py`
- Delete after migration: `backend/tests/services/test_report_sheet.py`

- [ ] **Step 1: Write failing renderer tests**

Cover the 12-image sample, department `colspan` values, result summaries, Token usage, XML escaping, >12-image bands, and parsing `<td id="...">` values from a fetched report table. The main assertion shape is:

```python
layout = render_report_document(current_items(), token_usage=complete_usage())
assert '<th colspan="2"' in layout.xml
assert '>安保<' in layout.xml
assert layout.xml.count("<table>") == 2  # report table + token table
assert [p.image_id for p in layout.images] == [f"image-{i}" for i in range(1, 13)]

cell_ids = parse_image_cell_ids(fetched_xml, expected_count=12)
assert cell_ids == ("cell-b", "cell-c", "cell-d", "cell-e", "cell-f", "cell-g",
                    "cell-h", "cell-i", "cell-j", "cell-k", "cell-l", "cell-m")
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/services/test_report_document.py -v`

Expected: collection fails because `app.services.report_document` does not exist.

- [ ] **Step 3: Implement the pure renderer and parser**

Define focused immutable values:

```python
@dataclass(frozen=True)
class DocumentItem:
    image_id: str
    filename: str
    image_path: Path
    verdict: str
    summary: str
    unverifiable_reason: str | None
    execution_status: str

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
```

Use `html.escape`, `MAX_IMAGES_PER_BAND = 12`, the existing filename department regex, `<colgroup>`, `<th colspan>`, and empty image-row `<td><p></p></td>` cells. Parse fetched XML with `xml.etree.ElementTree`, select rows whose first-column label is `巡检图片`, collect the empty paragraph block ID in each remaining cell, and raise `ValueError` unless the count equals `expected_count`.

- [ ] **Step 4: Run renderer tests and verify GREEN**

Run: `backend/.venv/bin/pytest backend/tests/services/test_report_document.py -v`

Expected: all renderer/parser tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/report_document.py backend/tests/services/test_report_document.py
git commit -m "feat: render native docx inspection tables"
```

### Task 2: Restore Docx transport through lark-cli

**Files:**
- Modify: `backend/app/services/feishu_client.py`
- Modify: `backend/app/services/lark_cli.py`
- Modify: `backend/tests/services/test_lark_cli.py`

- [ ] **Step 1: Replace spreadsheet client tests with failing Docx tests**

Test these protocol operations and exact argv/payloads:

```python
document = await client.create_document("巡检报告", "folder-1", "export-1")
await client.append_document_xml(document.id, "<table></table>", "layout")
xml = await client.fetch_document_xml(document.id)
block = await client.create_image_block(document.id, document.id, "doc-image:image-1")
await client.upload_docx_image(block.id, "原图.jpg", b"jpeg")
await client.move_document_block(document.id, "anchor-b", block.id)
```

Assert document creation uses `/open-apis/docx/v1/documents` with a stable hashed `client_token`; append uses `docs +update ... append`; fetch uses `docs +fetch --detail with-ids`; image block creation targets the document root; media upload uses `parent_type=docx_image`; and `block_move_after` moves the uploaded block behind the target cell anchor.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/services/test_lark_cli.py -v`

Expected: tests fail because the current protocol only exposes spreadsheet methods.

- [ ] **Step 3: Implement the Docx protocol and client**

Replace `SpreadsheetRef` with:

```python
@dataclass(frozen=True)
class DocumentRef:
    id: str
    url: str

@dataclass(frozen=True)
class BlockRef:
    id: str
```

Expose `create_document`, `append_document_xml`, `fetch_document_xml`, `create_image_block`, `upload_docx_image`, and `move_document_block`. Restore `_api()` and `_client_token()` so document and image-block creation remain idempotent. Set required connection scopes to:

```python
REQUIRED_REPORT_SCOPES = frozenset({
    "docx:document:create",
    "docx:document:readonly",
    "docx:document:write_only",
    "drive:file:upload",
})
```

- [ ] **Step 4: Run client tests and verify GREEN**

Run: `backend/.venv/bin/pytest backend/tests/services/test_lark_cli.py -v`

Expected: all lark-cli client and connection tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/feishu_client.py backend/app/services/lark_cli.py backend/tests/services/test_lark_cli.py
git commit -m "feat: publish reports through native docx blocks"
```

### Task 3: Make report export resumable at document-cell level

**Files:**
- Modify: `backend/app/services/reports.py`
- Modify: `backend/tests/services/test_reports.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/e2e_server.py`
- Modify: `backend/tests/api/test_reports.py`
- Modify: `backend/tests/persistence/test_tasks.py`
- Modify: `backend/app/repositories.py`

- [ ] **Step 1: Write failing export and retry tests**

Replace spreadsheet assertions with Docx behavior:

```python
assert exported.document_url == "https://example.feishu.cn/docx/document-1"
assert report_fixture.feishu.created_documents == 1
assert report_fixture.feishu.appended_xml.count("巡检图片") == 1
assert set(report_fixture.feishu.image_parent_ids) == {"cell-b", "cell-c", "cell-d"}
```

The retry test must fail one image upload, retry the same export, and assert one document, one XML append, one image block per image, completed uploads skipped, and the failed block reused. Keep the bounded-concurrency and new-version-without-new-inference tests.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/services/test_reports.py backend/tests/api/test_reports.py backend/tests/persistence/test_tasks.py -v`

Expected: spreadsheet-oriented export assertions fail.

- [ ] **Step 3: Implement document orchestration**

In `_write_export`:

```python
layout = render_report_document(tuple(self._document_item(item) for item in projection.items),
                                token_usage=projection.token_usage)
if export.document_id is None:
    document = await self.feishu.create_document(title, self._folder_token(), export.id)
    self._save_document(export.id, document.id, document.url)
if not self._operation_completed(export.id, "doc:layout"):
    await self.feishu.append_document_xml(export.document_id, layout.xml, "doc:layout")
    self._mark_operation_completed(export.id, "doc:layout")
await self._ensure_image_cells(export, layout)
await self._write_document_images(export, layout.images)
```

`_ensure_image_cells` fetches the document once when mappings are absent, parses paragraph anchors, and persists `doc-cell:<image_id>`. `_write_document_images` uses a semaphore of 4, creates/reuses `doc-image-block:<image_id>` at the document root, uploads to that block, moves it after the matching anchor, and records `doc-image:<image_id>` only after the move succeeds.

Keep `save_export_document`; remove spreadsheet-only layout constants and persistence helpers once no callers remain.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `backend/.venv/bin/pytest backend/tests/services/test_reports.py backend/tests/api/test_reports.py backend/tests/persistence/test_tasks.py -v`

Expected: all report, API, and persistence tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reports.py backend/app/repositories.py backend/tests/services/test_reports.py backend/tests/conftest.py backend/tests/e2e_server.py backend/tests/api/test_reports.py backend/tests/persistence/test_tasks.py
git commit -m "feat: export resumable docx table reports"
```

### Task 4: Restore document wording in the UI

**Files:**
- Modify: `frontend/src/features/tasks/TaskToolbar.tsx`
- Modify: `frontend/src/features/tasks/TaskPage.test.tsx`
- Modify: `frontend/src/features/history/HistoryPage.tsx`
- Modify: `frontend/src/features/history/HistoryPage.test.tsx`
- Modify: `frontend/e2e/inspection-flow.spec.ts`

- [ ] **Step 1: Write failing UI assertions**

Change expected accessible names to `打开飞书文档` and `打开<任务名>飞书文档`.

- [ ] **Step 2: Run affected tests and verify RED**

Run: `npm test -- src/features/tasks/TaskPage.test.tsx src/features/history/HistoryPage.test.tsx`

Expected: links still say `飞书表格`.

- [ ] **Step 3: Update user-facing wording**

Change only report artifact labels from `飞书表格` to `飞书文档`; keep URLs and behavior unchanged.

- [ ] **Step 4: Run affected tests and verify GREEN**

Run: `npm test -- src/features/tasks/TaskPage.test.tsx src/features/history/HistoryPage.test.tsx`

Expected: both files pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/tasks/TaskToolbar.tsx frontend/src/features/tasks/TaskPage.test.tsx frontend/src/features/history/HistoryPage.tsx frontend/src/features/history/HistoryPage.test.tsx frontend/e2e/inspection-flow.spec.ts
git commit -m "fix: describe report artifacts as documents"
```

### Task 5: Verify a real Docx report

**Files:**
- No source files are modified in this verification task.

- [ ] **Step 1: Run all automated verification**

```bash
backend/.venv/bin/pytest backend/tests
backend/.venv/bin/ruff check backend/app backend/tests
backend/.venv/bin/mypy backend/app
cd frontend && npm test && npm run build
```

Expected: all commands exit 0.

- [ ] **Step 2: Restart the application and generate a new report version**

Use task `25e7e2d6-70ff-4388-8957-66ab2beb453a`. Record inference attempts before generation, request `/report/new-version`, wait for `report_status=succeeded`, and confirm the attempt count is unchanged.

- [ ] **Step 3: Read back the live document**

Run `lark-cli docs +fetch --doc <new-url> --detail with-ids --as user --format json`. Assert `/docx/`, the three table labels, all 12 image tokens inside image-row cells, department `colspan` values `2/1/3/6`, 10 `合格`, 2 `不合格`, and both failure summaries.

- [ ] **Step 4: Verify the Drive parent folder**

List configured folder `Rm7XfJnfAlkZEUdpoDqc2SJUnmQ` with `drive files list` and assert the returned Docx item has that `parent_token`.
