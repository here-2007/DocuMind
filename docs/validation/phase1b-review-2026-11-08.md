# Phase 1B implementation verification — 2026-11-08

## Verdict

**Historical verdict: failed six checks at the reviewed commit.** All six now have fixes and passing regressions. [Current project progress](../../PROGRESS.md) records the later verification. At audit time the normal native/OCR/table/review flow worked, and the 34 source/configuration hashes in the [build evidence](phase1b-2026-11-08.json) matched commit `a7a9a7b7db59d922ae036c6cc270b0039c5dad51`.

Scope: the implemented English PDF/ruled-table subset under ADR-024, including source preservation, review, idempotency and OCR runtime. The excluded general-layout, unruled/ragged/merged raster-table and automatic cross-page linking features were not counted as defects. This was an implementation audit with three fresh-context, read-only reviewers; it was not the five-stage EGM document-review pipeline. Conversation turns were isolated for reviewers; host instruction-file exclusion was not independently established.

## Checks observed in this audit

| Check | Observed result |
|---|---|
| Full local suite with real OCR artifacts | 47 passed, zero skips, five dependency warnings, 18.98 seconds |
| `uv run ruff check backend scripts migrations` | Passed |
| `uv run alembic check` | No new upgrade operations detected |
| Expanded real Docker HTTP/Celery smoke | Passed text/table preview → correction/review → pinned old-version read → deletion; synthetic fixture cleaned |
| Current OCR manifest | Six file hashes and immutable model revisions verified |
| Real OCR engine initialization | 1.98 seconds in reviewer probe; blocked/recorded `requests.head` observed zero calls, which does not prove all possible network paths absent |
| Latest GitHub CI for the reviewed commit | [Succeeded](https://github.com/here-2007/DocuMind/actions/runs/42480651033), 47 passed in 30.85 seconds; workflow provisioned real models and set the OCR test root |
| Additional synthetic checks | Confirmed F1–F6 below; these are separate from the existing 47-test suite |

The code graph predates backend implementation and reports the relied-on paths as untracked. Findings use current source and executable checks. Database probes copied the existing isolated test fixture, which creates and removes a unique PostgreSQL database, queue namespace and object root. No production documents or application schemas were changed.

## F1 — Native table filtering drops text outside the table

Priority: major correctness fix. Location: [parser.py:169](../../backend/documind/parser.py#L169), with grouping in `_outside_table_words` at line 257.

The native adapter first removes table words, then groups the remaining words from a PDF line into one bounding box. `_canonical` filters that box by its center again. When the line has visible words on both sides of the table, the combined box centers inside the table and both outside fragments are discarded.

Reproduction: create a 500×400 page with a ruled table at x=150..350, y=100..220. On one PDF line at y=125, place `LEFT` before the table, `HEADER` inside it and `RIGHT` after it. Populate two table columns and three rows. Extract once with tables disabled, then with the default table adapter. The first extraction retains LEFT/RIGHT; the second omits both. Also reproduced with rotations 0/90/180/270. This violates the explicit adjacent-text preservation promise, and the review endpoint cannot add the missing elements.

Required regression: preserve each outside text fragment even when its line crosses a table on both sides. Avoid discarding already-filtered words through their later combined bounding box. Existing one-sided adjacent-text coverage is insufficient.

## F2 — Approval without corrections reorders rotated source text

Priority: major correctness fix. Location: [review.py:85](../../backend/documind/review.py#L85), calling the geometry sort in [parser.py:176](../../backend/documind/parser.py#L176).

Initial extraction orders text using unrotated geometry, then transforms stored boxes to the rotated preview. Review rebuilds the text by sorting those already-rotated boxes. This changes untouched reading order.

Reproduction: ingest a page with `First paragraph` at (40,60), `Second paragraph` at (40,180), and PDF rotation 180°. Submit a review with `confirm_all_pages=true` and `corrections=[]`. The job succeeds, but text changes from `First paragraph\nSecond paragraph\n` to `Second paragraph\nFirst paragraph\n`.

Required regression: no-edit approval preserves the existing reading order/raw text for all four rotations; explicit corrections alter only the intended content and dependent offsets/parent text. Preserve old version access as the current implementation already does.

## F3 — Reusing a completed review leaves latest and preview inconsistent

Priority: major correctness fix. Location: [repository.py:423](../../backend/documind/repository.py#L423), existing-job reuse and latest-pointer update.

Reproduction: from one original table version, complete review A with no corrections; then complete review B changing `12.50` to `13.50`. Submit A's exact body again with a **new** idempotency key C. The API reuses A's already-succeeded job and sets latest=A, but preview remains B. Redelivering A does nothing because completed jobs cannot be claimed. Default source reads therefore keep showing B despite the latest extraction reporting A as complete.

Required regression: define and implement a consistent transaction for selecting an already-completed version. A completed selected extraction must not leave a permanently different default preview. Preserve the existing guard against stale workers replacing newer previews.

## F4 — An accepted upload cannot replay during an OCR artifact outage

Priority: major recovery fix. Location: [repository.py:65](../../backend/documind/repository.py#L65), OCR/parser configuration loaded before the durable request-key lookup.

Reproduction: enable OCR with the existing verified local model root; upload a PDF with a fixed filename/body/idempotency key and observe 202. Make the configured model root unavailable, without changing the request body/key, then replay. Observed: 503 `OCR_MODEL_UNAVAILABLE` rather than the previously committed document/job. No model weights were removed in the test: its isolated configuration pointed to a missing temporary root.

The replay should identify already-accepted work without loading a new extraction profile. New uploads can still fail explicitly when the selected runtime is unavailable. Loading prerequisites before checking the ledger breaks that recovery path and can obscure whether an earlier upload succeeded.

Required regression: same-key/body replays the committed resource during missing/changed OCR dependencies; differing bodies still conflict, new uploads still fail unavailable, and no duplicate resources are created.

## F5 — Complete single-row or single-column raster grids are missed

Priority: normal table coverage fix. Location: [tables.py:71](../../backend/documind/tables.py#L71).

The adapter requires at least three vertical and three horizontal rulings, which silently excludes grids with only one row or one column. Those are complete rectangular grids within the documented subset, not excluded merged/unruled/ragged layouts.

Reproduction: generate a clean 600×600 grayscale image with a complete black grid at x/y=60..540. A 1×2 grid and a 2×1 grid each return zero tables; the otherwise equivalent 2×2 grid returns one table/four cells. This was a deterministic raster-adapter test, not a full OCR-model check. OCR paragraphs can survive, but the promised table-cell geometry/identity is absent.

Required regression: cover 1×2 and 2×1 ruled grids and retain their cell coordinates/text. If size constraints are required, enforce a justified rule without silently contradicting the declared scope.

## F6 — Shared cell boundaries duplicate an OCR line

Priority: normal table assignment fix. Location: [tables.py:84](../../backend/documind/tables.py#L84) and inclusive [contains at line 125](../../backend/documind/tables.py#L125).

Reproduction: use a complete 600×600 2×2 grid with dividers at x/y=60,300,540. Supply one valid OCR-style line `LEFT RIGHT`, confidence 0.9, normalized bbox `[200/600,140/600,400/600,180/600]`. Its center lies exactly on x=300. Both adjacent cells accept the same line and canonical text becomes `LEFT RIGHT\tLEFT RIGHT\n\t\n`.

This is an executed adapter-input boundary test, **not evidence that the selected OCR model produced this box**, nor a frequency estimate. It establishes that an accepted geometric input can duplicate source text into different cells.

Required regression: assign a line at most once, or explicitly preserve an ambiguous spanning line for review. Do not silently clone the full text into adjacent cell values.

## Clarifications and remaining limits

- Explicit `is_header:null` currently retains an existing flag. The contract does not clearly distinguish “leave unchanged” from “set unknown”; clarify before treating this as an additional defect.
- Replaying an older review key returns that job alongside current document metadata. Clarify whether the response is intended to be a request-pinned snapshot or live document summary. F3 above is stronger: it uses a new key and produces inconsistent pointers.
- OCR provisioning/runtime/CI review found no additional actionable defect in its bounded checks. That does not establish general scan accuracy, all offline network behavior, target 2-vCPU capacity or the optional OCR Docker runtime.
- No fixes were applied or pushed. Add permanent regression tests with the fixes, then rerun the affected checks before restoring Phase 1B sign-off. No new architecture decision was made by this audit.

## Reproduction records from this session

Temporary files are diagnostics, not portable repository dependencies:

- F1: `/tmp/documind-phase1b-parser-audit/content_loss.py`; broader rotated/cropped checks in `repro.py` in the same folder.
- F2/F3: `/var/folders/_l/f2y24x9s6rzbqr4s0bbws1vh0000gn/T/documind-review-audit-6iqb7u39/test_review_audit.py`, tests `test_rotated_no_changes` and `test_reuse_config_promotes_preview`; both expected invariants failed. Other assertions in that temporary file test the contract ambiguities above and are not counted as confirmed findings.
- F4: `/var/folders/_l/f2y24x9s6rzbqr4s0bbws1vh0000gn/T/documind-1b-http-audit-77_lgb35/test_http_audit.py`; its expected-202 assertion failed with 503.
- F5/F6: `/tmp/documind-phase1b-parser-audit/raster.py`; observed table counts and duplicated raw text as described above.

The reproduction descriptions above preserve the fixtures' essential inputs if temporary files are removed. [PROGRESS.md](../../PROGRESS.md) owns current status; [IMPLEMENTATION.md](../../IMPLEMENTATION.md) records remaining gates.
