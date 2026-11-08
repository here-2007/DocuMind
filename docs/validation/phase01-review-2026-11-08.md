# Phase 0 + Phase 1A correctness review — 2026-11-08

Historical review verdict: the local native-PDF vertical slice worked, but F1–F3 required fixes before sign-off. **Follow-up: all three are now fixed with permanent regressions; current results are in [PROGRESS.md](../../PROGRESS.md).** No application code/configuration was changed during this review. This report supplements, rather than replaces, the initial build's [environment/hash artifact](phase01-2026-11-08.json).

## Observed checks

| Check run during this review | Result |
|---|---|
| `uv run pytest -q` | 28 passed; six dependency deprecation warnings |
| `uv run ruff check backend scripts migrations` | Passed |
| `uv run alembic check` | No new upgrade operations detected |
| `uv run python scripts/smoke.py` | Passed real HTTP upload → Redis/Celery → PostgreSQL → PNG/text preview → deletion; synthetic fixture deleted |
| Three extra diagnostic tests outside the repository | Three failed assertions reproducing F1–F3 below; disposable PostgreSQL database/store/queue |
| Logging configuration probe in API container | Effective `documind` level WARNING; INFO disabled; zero own handlers |

The source/index coverage check showed backend paths untracked by the older code graph. Conclusions use current source, contracts and runtime checks, not graph absence. Review included API/configuration, repository/job lifecycle, parser/storage, schema/migration, existing tests, Compose/CI and milestone contracts. Clean copied-source startup and remote CI were not rerun; public deployment/load/model behavior were not evaluated.

## F1 — Source text can be returned after deletion commits

Priority: high for the promised deletion visibility boundary.

Location: [repository.py](../../backend/documind/repository.py#L198), `source` and `element_list`; [api.py](../../backend/documind/api.py#L255), page/element responses.

`source` checks document lifecycle first, then reads version/page rows without checking visibility again. `element_list` performs another separate element query after that initial check. Unlike image/original reads, text routes do not perform a final lifecycle check.

Reproduction: ingest a synthetic native-text PDF using the existing isolated repository/client fixtures. Install a SQLAlchemy `before_cursor_execute` listener on the repository engine. Once, immediately before the `SELECT ... FROM document_pages`, call `repo.delete(workspace_id, document_id)` on its separate transaction. Request `/pages/1` and remove the listener in `finally`.

Observed: deletion committed before the page SELECT, yet the response was HTTP 200 containing source text. Expected under the deletion contract: `410 SOURCE_UNAVAILABLE`. This concerns content not yet delivered; already delivered bytes cannot be recalled. The analogous element race is supported by source inspection, not a separate executed diagnostic.

Required regression: deterministically interleave deletion during page and element reads and verify no source content is returned after the visibility recheck observes deletion. Keep normal reads and wrong-workspace/version rejection working. Do not weaken the deletion contract to accommodate the defect.

## F2 — Element pagination encodes the document key

Priority: normal; blocks reliable element traversal and future source viewers.

Location: [api.py](../../backend/documind/api.py#L182), `envelope`; [repository.py](../../backend/documind/repository.py#L223), `element_list`.

The shared envelope prefers `document_id` over `id`. Element rows contain both, but the query orders and filters by `elements.id`.

Reproduction: create a PDF with three separated text blocks; ingest it and request `/elements?limit=1`. Base64-decode `next_cursor` and compare it to the returned element's `id`.

Observed: cursor equals the document UUID, not the returned element UUID. Following it can skip elements, repeat them or terminate early depending on UUID ordering. The diagnostic assertion comparing cursor and element IDs failed.

Required regression: follow every cursor through a multi-element fixture; all expected IDs must occur exactly once and traversal must terminate. Retain document-list pagination coverage.

## F3 — Structured request records are suppressed

Priority: normal; Phase 0 explicitly requires structured request logging.

Location: [api.py](../../backend/documind/api.py#L25), logger initialization and INFO request event; [compose.yaml](../../compose.yaml#L47), documented Uvicorn startup.

No application logging configuration enables the `documind` logger. Uvicorn's default configuration leaves its effective level at WARNING. `.env.example` advertises `LOG_LEVEL=INFO`, but settings/application startup do not consume it. Compose also disables Uvicorn access logging.

Reproduction: apply Uvicorn's default logging configuration, then inspect `logging.getLogger("documind").isEnabledFor(logging.INFO)`. Independently run the same configuration probe in the API container.

Observed in both: INFO is disabled; the container reported `effective_level=WARNING`, `info_enabled=False`, `own_handlers=0`. The per-request JSON INFO call therefore emits no request record with the supplied startup command. This finding does not assert that ERROR records are suppressed.

Required regression: start the app with its real logging configuration, make a request and capture an emitted structured record containing the response's request ID. Verify no tokens or document content are logged.

## Follow-up

Convert these diagnostics into permanent regression tests while fixing the defects; the existing 28 passing tests do not cover them. Rerun the suite, lint, migration check and live native smoke after the changes. [PROGRESS.md](../../PROGRESS.md) owns current completion status; [IMPLEMENTATION.md](../../IMPLEMENTATION.md) includes the remaining sign-off gates.
