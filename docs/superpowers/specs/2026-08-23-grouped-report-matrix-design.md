# Grouped Inspection Report Matrix Design

> Superseded by `2026-08-23-dynamic-spreadsheet-report-design.md`. The corrected requirement is a native Feishu spreadsheet whose first data row contains images and whose second data row contains the corresponding results.

## Context

The current Feishu report presents a summary followed by one detailed section per image. The source spreadsheet arranges the inspected photos horizontally and groups adjacent photos under department headings. Readers need the generated report to preserve that visual reading pattern without exposing source cell coordinates or requiring the source spreadsheet to remain available.

For the current task, the ordered groups are:

- 安保: 2 images
- 工程人员: 1 image
- 客房: 3 images
- 前厅: 6 images

## Goals

- Add a compact, native Feishu Docx comparison table near the top of every report.
- Keep the original upload order.
- Merge adjacent department headings across their result columns.
- Put each image's verdict in the second row.
- Keep the existing per-image evidence and image sections below the table.
- Generate a new report version for the existing completed task without rerunning inference.

## Non-goals

- Do not modify the source spreadsheet.
- Do not show spreadsheet coordinates such as `D2` or `O2` in the report.
- Do not add a runtime dependency on the source spreadsheet or its URL.
- Do not remove the detailed findings, evidence text, or report images.

## Report layout

The report keeps its title, AI-review notice, task metadata, aggregate counts, issue summary, detailed image sections, and trace section.

Between the aggregate summary and the detailed sections, add a heading named `巡检结果总览` and a two-row table:

1. The first cell is `巡检结果` and spans both rows vertically.
2. The remaining first-row cells are department headings. Adjacent images from the same department share one horizontally merged cell.
3. The second row has one cell per image. Each cell contains its department-local sequence number and Chinese verdict, for example `01\n合格` or `03\n不合格`.
4. Verdict labels are `合格`, `不合格`, `无法判断`, and `执行失败`.
5. The header uses a restrained gray background. Verdict text is bold, with green for pass, red for fail or execution failure, and amber for unverifiable.

For the current report, the merged spans are `安保 × 2`, `工程人员 × 1`, `客房 × 3`, and `前厅 × 6`. No source URL or cell reference is printed.

The existing detailed section headings become reader-friendly labels such as `安保 01｜合格`; the original image is still shown directly beneath its result and findings.

## Metadata derivation and fallback

The report derives display metadata locally from filenames shaped like `行02_D列_安保_01.png`:

- department: `安保`
- department-local sequence: `01`
- upload order: the immutable task image position, not the spreadsheet column text

Spreadsheet row and column fragments are parsed only to recognize this filename convention and are never rendered. Consecutive items with the same department are grouped. If a filename does not match, the report falls back to `巡检图片` plus a stable 1-based sequence and keeps the original filename in the detailed section.

To remain readable for tasks larger than this example, a comparison band contains at most 12 image-result columns. Larger tasks produce additional two-row bands, repeating the grouped headings for each band.

## Implementation approach

The active `LarkCliFeishuClient` gains one focused operation for appending Docx XML content. The report builder renders the comparison matrix as a native `<table>` using documented `rowspan` and `colspan` attributes, then asks the client to append it after the textual overview and before image details.

This approach is preferred over creating and filling individual table-cell blocks because it keeps the matrix to one additional CLI operation and avoids materially slowing report export. The existing export operation ledger records the matrix under its own stable operation key so completed work is skipped on retries.

All report text inserted into XML is escaped before interpolation. The table renderer is a pure function so grouping, fallback labels, batching, merge spans, and status translation can be tested without a network call.

## Failure and recovery behavior

- A failed matrix append fails the report export through the existing `report_export_failed` path.
- Retrying continues in the same document and skips any operation already marked complete.
- Existing reports are never overwritten. The current completed task is regenerated with the existing new-version endpoint.
- No VLM inference is triggered by report regeneration.

## Verification

- Unit tests cover filename parsing, department grouping, 12-column batching, fallback labels, translated verdicts, XML escaping, and the exact two-row merge structure.
- CLI adapter tests verify argv-only invocation of `docs +update`, user identity, stdin content, and failure propagation.
- Report tests verify operation ordering and retry behavior.
- Full backend lint, type checking, backend tests, frontend tests, and frontend build must pass.
- The regenerated Feishu document is fetched read-only to confirm one grouped two-row matrix, 12 result cells, 12 detailed sections, and 12 images.
- The inference-attempt count must remain unchanged at 12.
