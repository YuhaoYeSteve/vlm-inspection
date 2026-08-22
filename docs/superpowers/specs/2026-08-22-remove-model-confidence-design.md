# Remove Model-Self-Reported Confidence Design

## Context

The current inspection contract asks the vision model to generate `overall_confidence` and a
per-finding `confidence`. These values are model-authored text fields, not calibrated
probabilities or API-provided token likelihoods. Displaying them as percentages overstates their
meaning and can mislead reviewers.

## Approved approach

Remove confidence values across the full product surface:

- New Ark requests no longer include either confidence field in the JSON Schema.
- Backend result contracts no longer expose `overall_confidence` or finding-level `confidence`.
- The task detail UI and single-image test UI no longer render confidence percentages.
- Feishu reports no longer include overall confidence.
- No replacement score or evidence-quality grade is introduced in this change.

The verdict remains deterministic: a visible failed finding produces `fail`; all visible findings
passing produces `pass`; an invalid image or no visible findings produces `unverifiable`.

## Backward compatibility

Existing SQLite result JSON may still contain the two legacy fields. The backend will remove only
`overall_confidence` and nested finding `confidence` before strict Pydantic validation. All other
unknown fields remain forbidden. Existing task pages can continue to read their raw snapshots, and
existing reports can be regenerated without a data migration.

## Verification

- Domain tests confirm the new JSON Schema contains no confidence fields.
- Domain tests confirm legacy stored results still parse and serialize without confidence.
- Frontend tests confirm result panels render verdicts and evidence without confidence text.
- Report tests confirm generated report blocks contain no confidence label.
- Full backend, frontend, build, and end-to-end suites remain green.
