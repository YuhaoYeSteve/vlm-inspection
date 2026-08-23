# Full Model Output Report Row Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a readable fourth table row containing every business-facing field from each image's structured model result, then generate a new live report version without rerunning inference.

**Architecture:** Extend the pure Docx report projection with immutable finding data, render the full hierarchy inside each matching table cell, and keep transport/image placement unchanged. The current completed task is exported through the existing new-version path so the old document is preserved and stored inference results are reused.

**Tech Stack:** Python 3.12, Pydantic, FastAPI, SQLAlchemy, pytest, lark-cli, Feishu Docx XML/OpenAPI, React/Vitest/Playwright for regression verification.

---

### Task 1: Render the complete model-output row

**Files:**
- Modify: `backend/app/services/report_document.py`
- Modify: `backend/tests/services/test_report_document.py`

- [ ] **Step 1: Write failing renderer tests**

Add a `DocumentFinding` test fixture and extend `item()` with `image_valid` and `findings`. Assert that one report band contains the fourth-row label and all readable fields:

```python
finding = DocumentFinding(
    rule_name="工牌佩戴",
    status="fail",
    observation="左胸未见工牌 <需复核>",
    visual_evidence="左胸区域清晰 & 无遮挡",
)
layout = render_report_document(
    (
        item(
            1,
            "行02_D列_安保_01.png",
            "fail",
            "发现工牌问题",
            image_valid=True,
            findings=(finding,),
        ),
    ),
    token_usage=complete_usage(),
)

assert ">模型完整输出<" in layout.xml
assert "图片有效性：</b>有效" in layout.xml
assert "总体结论：</b>不合格" in layout.xml
assert "总结：</b>发现工牌问题" in layout.xml
assert "检查项 1：工牌佩戴｜不合格" in layout.xml
assert "左胸未见工牌 &lt;需复核&gt;" in layout.xml
assert "左胸区域清晰 &amp; 无遮挡" in layout.xml
assert "schema_version" not in layout.xml
assert "rule_id" not in layout.xml
assert "confidence" not in layout.xml
```

Add focused cases for `not_visible → 未清晰入镜`, optional `不可判断原因`, no findings (`检查项：无`), failed execution (`无模型输出`), and two report bands each containing `模型完整输出`.

- [ ] **Step 2: Run renderer tests and verify RED**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_report_document.py -q
```

Expected: FAIL because `DocumentFinding` and the fourth row do not exist.

- [ ] **Step 3: Add immutable finding data and renderer helpers**

In `report_document.py`, add:

```python
@dataclass(frozen=True)
class DocumentFinding:
    rule_name: str
    status: str
    observation: str
    visual_evidence: str
```

Extend `DocumentItem`:

```python
image_valid: bool | None
findings: tuple[DocumentFinding, ...]
```

Add explicit mappings:

```python
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
```

Implement `_model_output_cell(item)` with multiple `<p>` blocks. A successful result renders image validity, verdict, summary, optional unverifiable reason, and every finding's name/status, observation, and visual evidence. Escape every dynamic value with `html.escape`. A missing result renders `无模型输出` and `执行状态：执行失败`; an empty finding tuple renders `检查项：无`.

Update `_render_band()` so image/result columns are 240 pixels wide and append:

```python
model_output_cells = "".join(_model_output_cell(item) for item in items)

"<tr>"
'<td background-color="light-gray" vertical-align="middle">'
'<p align="center"><b>模型完整输出</b></p></td>'
f"{model_output_cells}</tr>"
```

- [ ] **Step 4: Run renderer tests and verify GREEN**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_report_document.py -q
```

Expected: all renderer/parser tests pass.

- [ ] **Step 5: Commit the renderer change**

```bash
git add backend/app/services/report_document.py backend/tests/services/test_report_document.py
git commit -m "feat: render full model output in report table"
```

### Task 2: Project normalized findings into the document renderer

**Files:**
- Modify: `backend/app/services/reports.py`
- Modify: `backend/tests/services/test_reports.py`

- [ ] **Step 1: Write a failing report-projection test**

Extend `test_report_creates_docx_table_and_places_matching_images` to verify the rendered XML receives all normalized finding fields:

```python
assert ">模型完整输出<" in rendered
assert "检查项 1：badge｜不合格" in rendered
assert "观察：</b>result-2" in rendered
assert "视觉证据：</b>visible-result-2" in rendered
assert "图片有效性：</b>无效" in rendered
assert "不可判断原因：</b>image is obscured" in rendered
```

- [ ] **Step 2: Run the focused report test and verify RED**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_reports.py::test_report_creates_docx_table_and_places_matching_images -q
```

Expected: FAIL because `_document_item()` does not project `image_valid` or `findings`.

- [ ] **Step 3: Map the complete normalized result**

Import `DocumentFinding` and extend `_document_item()`:

```python
image_valid=result.image_valid if result is not None else None,
findings=(
    tuple(
        DocumentFinding(
            rule_name=finding.rule_name,
            status=finding.status.value,
            observation=finding.observation,
            visual_evidence=finding.visual_evidence,
        )
        for finding in result.findings
    )
    if result is not None
    else ()
),
```

Keep the existing verdict, summary, unverifiable reason, execution status, image placement, and upload/binding behavior unchanged.

- [ ] **Step 4: Run report tests and verify GREEN**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_reports.py backend/tests/services/test_report_document.py -q
```

Expected: all focused report tests pass.

- [ ] **Step 5: Commit the projection change**

```bash
git add backend/app/services/reports.py backend/tests/services/test_reports.py
git commit -m "feat: project model findings into Feishu report"
```

### Task 3: Verify the application and generate the live report version

**Files:**
- No source-file changes expected.
- Runtime state: `~/.vlm-inspector/app.db`
- Remote output: configured Feishu report folder

- [ ] **Step 1: Run complete automated verification**

Run:

```bash
backend/.venv/bin/ruff check backend/app backend/tests
backend/.venv/bin/mypy backend/app
backend/.venv/bin/pytest backend/tests -q
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run e2e
```

Expected: every command exits 0.

- [ ] **Step 2: Record the inference-attempt baseline**

Run a read-only SQLite query for task `25e7e2d6-70ff-4388-8957-66ab2beb453a` and verify there are 12 images with a total `attempt_count` of 12.

- [ ] **Step 3: Generate a new report version from stored results**

Instantiate the existing `ReportBuilder` with the configured database and `LarkCliFeishuClient`, then run:

```python
await builder.export("25e7e2d6-70ff-4388-8957-66ab2beb453a", new_version=True)
```

Expected: a new export ID and Docx URL with status `succeeded`. This path reads saved `inference_results` and does not invoke the inspection service.

- [ ] **Step 4: Verify the live Docx and unchanged inference count**

Fetch the new document with `lark-cli docs +fetch --detail full --as user`. Verify:

- 12 `<img>` blocks contain both `src` and `href`.
- The report table contains one `模型完整输出` row and 12 model-output cells.
- Representative cells contain `图片有效性`, `总体结论`, `总结`, `检查项`, `观察`, and `视觉证据`.
- SQLite still reports 12 images and total `attempt_count = 12`.

- [ ] **Step 5: Report the new document URL and commits**

Provide the user the new Feishu Docx URL, confirm that the old report remains intact, and cite the renderer/projection commits plus the verification results.
