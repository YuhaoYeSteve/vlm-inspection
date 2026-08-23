# Full Model Output Report Row Design

## Context

The native Feishu Docx inspection matrix currently has a merged department header row, an image row, and an inspection-result row. The result row only shows the overall verdict and a short summary. Readers also need the complete structured model output directly beneath that row so they can compare every field against the corresponding image without leaving the matrix.

## Goals

- Keep the existing department headers, image row, and inspection-result row unchanged.
- Add a fourth row named `模型完整输出` immediately below `巡检结果` in every report band.
- Render the complete business-facing hierarchy for each image in its matching column.
- Apply the layout to future reports and generate a new report version for the current completed task without rerunning model inference.

## Non-goals

- Do not print raw JSON.
- Do not expose technical fields such as `schema_version` or `rule_id`.
- Do not restore legacy confidence fields.
- Do not overwrite or destructively rebuild the existing report document.
- Do not change the model prompt, result schema, or verdict normalization rules.

## Report layout

Each report band contains four rows:

1. Merged department headers.
2. `巡检图片`, with one image per column.
3. `巡检结果`, with the existing Chinese verdict and summary.
4. `模型完整输出`, with the readable structured output for the same image.

Image/result columns use a wider fixed width of 240 pixels so the detailed text remains readable. The document may require horizontal scrolling for a 12-image band; preserving direct image-to-result alignment takes priority over fitting all columns into the viewport.

## Model-output cell format

For a successful inference, each cell renders fields in this order:

```text
图片有效性：有效
总体结论：合格
总结：……
不可判断原因：……        # only when present

检查项 1：头发与发型｜合格
观察：……
视觉证据：……

检查项 2：面部仪容｜未清晰入镜
观察：……
视觉证据：……
```

Status labels are translated as follows:

- Overall `pass` / `fail` / `unverifiable` → `合格` / `不合格` / `无法判断`.
- Finding `pass` / `fail` / `not_visible` → `合格` / `不合格` / `未清晰入镜`.
- `image_valid` → `有效` / `无效`.

Every finding includes `rule_name`, `status`, `observation`, and `visual_evidence`. Technical identifiers and removed confidence values are not rendered. All dynamic text is XML-escaped.

If an image execution failed and has no validated model result, the cell displays `无模型输出` and the execution status. An empty findings list displays `检查项：无`.

## Implementation design

The report projection already retains each normalized `InspectionResult`. The document projection will be expanded with immutable finding data required by the renderer. A focused pure renderer will produce one structured model-output cell per `DocumentItem`, and `_render_band` will append the fourth row after the current result row.

The existing image placement and binding flow remains unchanged. The report continues to split after 12 images, and every band repeats all four rows. The current task will be exported through the existing `new_version=True` path, creating a new Docx in the configured report folder while reusing stored inference results.

## Failure and recovery behavior

- Missing validated output produces an explicit no-output cell instead of an empty cell.
- A report export failure follows the existing `report_export_failed` path and can be retried without rerunning inference.
- Existing report documents remain intact; the current task receives a separate new report version.

## Verification

- Renderer tests cover the fourth-row label, overall fields, all finding fields, Chinese status mappings, XML escaping, missing-output behavior, and multi-band reports.
- Report-builder tests verify the complete result is projected into the document renderer.
- Full backend lint, type checking, and tests pass.
- Frontend unit tests, build, and end-to-end flow pass.
- The regenerated live document contains 12 images, 12 inspection-result cells, and 12 complete model-output cells.
- The current task's inference-attempt count remains 12.
