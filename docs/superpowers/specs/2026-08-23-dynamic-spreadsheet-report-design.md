# Dynamic Spreadsheet Inspection Report Design

## Context

The previous implementation added a result-only matrix to a Feishu Docx report. That does not match the intended reading experience. The supplied spreadsheet is a visual reference only: generated reports must use the current task's images and inspection results, without copying data from or depending on the reference spreadsheet.

The report artifact will therefore become a native Feishu spreadsheet created in the configured report folder.

## Approved layout

Each comparison band has one header row and two data rows:

| Row | First column | Remaining columns |
| --- | --- | --- |
| Header | `巡检项` | Department labels, merged across consecutive images in the same department |
| First data row | `巡检图片` | The actual images uploaded for this task, one image per cell |
| Second data row | `巡检结果` | The result for the image directly above it |

For the current 12-image task, the header spans are `安保 × 2`, `工程人员 × 1`, `客房 × 3`, and `前厅 × 6`. These values are derived at runtime and are not hard-coded.

Result cells use these display rules:

- Pass: `合格`
- Fail: `不合格` followed by the task result summary on a new line
- Unverifiable: `无法判断` followed by the reason or summary on a new line
- Execution failure: `执行失败` followed by the stored error state on a new line

The header has the same restrained gray fill and thin borders as the reference. All image and result cells are centered vertically and horizontally. Result text wraps, and pass/fail states use green/red text. Column widths and row heights follow the reference proportions: a narrow row-label column, equal image columns, a compact header row, a taller image row, and an auto-wrapped result row.

At most 12 images appear in one band. Larger tasks continue below in another three-row band, preserving upload order and repeating the row labels and grouped department headers.

## Data derivation

The report uses only the immutable task snapshot and stored inspection results.

- Image order comes from `TaskImage.position`.
- Image bytes come from each task image's stored thumbnail path.
- Department and local sequence are parsed from filenames shaped like `行02_D列_安保_01.png`.
- Adjacent images with the same department are merged into one header cell.
- A non-matching filename uses department `巡检图片` and a stable one-based sequence.
- The source spreadsheet URL, source cells, prompts, sample rows, and source images are never read during report generation and never appear in the output.

## Feishu integration

`LarkCliFeishuClient` gains spreadsheet-specific operations:

1. Create a workbook in the configured destination folder with the text values, styles, row heights, column widths, borders, and merge ranges in one request.
2. Return the spreadsheet token, URL, and first worksheet ID.
3. Insert each task image into its exact image cell with the native single-cell image operation.
4. Read back the generated ranges for verification.

The existing report API and `report_url` field remain unchanged; the URL now points to a Feishu spreadsheet. Existing Docx reports remain untouched.

The conversation-based authorization flow adds the required spreadsheet create, metadata, write, and read scopes. Users are not asked to paste app IDs, secrets, folder tokens, or spreadsheet tokens into settings.

## Export recovery and performance

The report export record stores the created spreadsheet token and worksheet ID. The operation ledger records one stable key for workbook layout creation and one key per image cell.

- A retry reuses the same spreadsheet instead of creating a duplicate.
- Successfully inserted images are skipped on retry.
- A new-version request always creates a separate spreadsheet.
- Report regeneration reads stored results and does not call the VLM.
- Image-cell uploads use bounded concurrency so a 12-image report does not serialize avoidable network waits.

## Rejected alternatives

- Copying the reference spreadsheet was rejected because it would retain unrelated source content and create a runtime dependency on that file.
- A Docx table was rejected because its image-cell sizing and spreadsheet-like layout cannot match the requested format reliably.
- Importing a generated XLSX was rejected because import fidelity and resumability are less predictable than native sheet operations.

## Failure behavior

- Missing spreadsheet authorization pauses the export through the existing Feishu reauthorization path and exposes the conversation authorization action.
- A failed workbook or image write marks the export failed with the existing generic report error code; retry resumes from the operation ledger.
- No partial report is advertised as successful.

## Verification

Automated tests cover:

- dynamic grouping and merge ranges;
- image-row and result-row cell mapping;
- pass, fail, unverifiable, and execution-failure result text;
- 12-column banding and fallback filenames;
- CLI command arguments, stdin/file handling, and failure propagation;
- retry behavior without duplicate workbooks or image writes;
- unchanged report API behavior and zero new inference attempts.

The regenerated live report is read back to confirm:

- a native spreadsheet exists in the configured folder;
- the current task produces the exact `2 / 1 / 3 / 6` group spans;
- 12 task images occupy the first data row;
- 12 matching results occupy the second data row;
- the two failing cells contain their summaries;
- no source spreadsheet content or coordinates appear;
- the inference-attempt count remains 12.
