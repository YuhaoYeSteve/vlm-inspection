# Remove Model-Self-Reported Confidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove model-authored confidence values from new inspection results, the UI, and Feishu reports while keeping legacy stored results readable.

**Architecture:** The Pydantic result contract remains the source of the Ark JSON Schema. A narrow pre-validation compatibility shim removes only the two legacy confidence keys before strict validation. Frontend and report renderers consume the smaller contract and display evidence without a percentage.

**Tech Stack:** Python 3.12, Pydantic 2, pytest, React 19, TypeScript, Vitest.

---

### Task 1: Remove confidence from the backend contract

**Files:**
- Modify: `backend/tests/domain/test_results.py`
- Modify: `backend/app/domain/results.py`
- Modify: backend test fixtures that construct `InspectionResult` and `Finding`

- [ ] **Step 1: Write failing contract and compatibility tests**

Add tests asserting that the generated JSON Schema contains neither `overall_confidence` nor finding-level `confidence`, and that a legacy payload containing both fields validates but serializes without them.

```python
def test_result_schema_does_not_request_model_confidence() -> None:
    schema = json.dumps(InspectionResult.model_json_schema())
    assert "overall_confidence" not in schema
    assert '"confidence"' not in schema


def test_legacy_confidence_fields_are_ignored() -> None:
    result = InspectionResult.model_validate(legacy_payload)
    serialized = result.model_dump()
    assert "overall_confidence" not in serialized
    assert "confidence" not in serialized["findings"][0]
```

- [ ] **Step 2: Run the domain tests and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/domain/test_results.py -v`

Expected: FAIL because confidence remains in the schema and serialized result.

- [ ] **Step 3: Implement the compatibility shim and smaller contract**

Remove both confidence fields. Add a `mode="before"` validator on `InspectionResult` that copies mapping inputs, removes `overall_confidence`, removes `confidence` from nested finding mappings, and leaves every other unknown key for `extra="forbid"` to reject.

```python
@model_validator(mode="before")
@classmethod
def discard_legacy_confidence(cls, value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    cleaned = dict(value)
    cleaned.pop("overall_confidence", None)
    findings = cleaned.get("findings")
    if isinstance(findings, list):
        cleaned["findings"] = [
            {key: item_value for key, item_value in finding.items() if key != "confidence"}
            if isinstance(finding, Mapping)
            else finding
            for finding in findings
        ]
    return cleaned
```

- [ ] **Step 4: Remove confidence arguments from backend fixtures and run backend tests**

Run:

```bash
backend/.venv/bin/pytest backend/tests -q
backend/.venv/bin/ruff check backend/app backend/tests
backend/.venv/bin/mypy backend/app
```

Expected: all checks pass.

### Task 2: Remove confidence from user-visible output

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/features/tasks/ResultPanel.tsx`
- Modify: `frontend/src/features/tasks/TaskPage.test.tsx`
- Modify: `frontend/src/features/templates/TemplatesPage.test.tsx`
- Modify: `backend/app/services/reports.py`
- Modify: `backend/tests/services/test_reports.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing UI and report tests**

In the frontend result test, assert no percentage is rendered. In the report test, retain created block payloads and assert the serialized content contains no `置信度` label.

```tsx
expect(screen.queryByText("94%")).not.toBeInTheDocument();
```

```python
assert "置信度" not in json.dumps(report_fixture.feishu.block_payloads, ensure_ascii=False)
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
npm --prefix frontend test -- TemplatesPage.test.tsx
backend/.venv/bin/pytest backend/tests/services/test_reports.py -v
```

Expected: FAIL because both renderers still emit confidence.

- [ ] **Step 3: Remove display fields and update fixtures**

Delete confidence properties from TypeScript contracts and fixtures. Remove the percentage `<output>` and finding confidence `<dl>` row from `ResultPanel`. Change report detail text from verdict/confidence/summary to verdict/summary only.

```python
self._text_block(f"结论：{result.verdict.value}\n摘要：{result.summary}")
```

- [ ] **Step 4: Update operator documentation and verify frontend/report tests**

Remove the confidence claim from `README.md`, then run:

```bash
npm --prefix frontend test
npm --prefix frontend run build
backend/.venv/bin/pytest backend/tests/services/test_reports.py -v
```

Expected: all tests and the TypeScript production build pass.

### Task 3: Verify and restart the local application

**Files:**
- No additional source files.

- [ ] **Step 1: Confirm no active contract or UI references remain**

Run:

```bash
rg -n "overall_confidence|finding\.confidence|置信度" backend/app frontend/src README.md
```

Expected: no active product-code matches.

- [ ] **Step 2: Run the complete verification suite**

Run:

```bash
backend/.venv/bin/ruff check backend/app backend/tests
backend/.venv/bin/mypy backend/app
backend/.venv/bin/pytest backend/tests -q
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run e2e
```

Expected: all commands exit successfully.

- [ ] **Step 3: Commit and restart**

```bash
git add backend frontend README.md docs/superpowers
git commit -m "refactor: remove model confidence scores"
./scripts/start.sh
```

Verify `GET /api/health` returns `{"status":"ready"}` and an existing task opens without confidence percentages.
