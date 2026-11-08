# Project progress

Current implementation status and dated validation. [MEMORY.md](MEMORY.md) holds durable lessons; [DECISIONS.md](DECISIONS.md) holds reasoning; [IMPLEMENTATION.md](IMPLEMENTATION.md) owns milestone scope and acceptance gates. Update this file after meaningful work; historical checks are not a new test run.

## Current state

Phase 0/1A review fixes and bounded Phase 1B PDF extraction are implemented locally. Available flow: upload → durable job → native/OCR text and ruled tables → source preview → optional immutable human review/correction. Search, generation, Next.js and the public QA/research platform are not implemented.

| Milestone | State |
|---|---|
| Phase 0: backend, containers, migrations and test harness | Verified locally; request logging regression fixed; optional frontend shell deferred |
| Phase 1A: native-PDF ingestion and source preview | Verified locally; deletion/read and pagination regressions fixed |
| Phase 1B: OCR and table adapters | Implemented/tested for English CPU OCR, native ruled tables and complete raster grids; human review required |
| Phase 2: hybrid-v3 retrieval and custom-endpoint baseline | Next implementation milestone; not started |
| Phases 3/4/7: Next.js, structured comparisons and reporting | Pending; product releases remain independent of training |
| Phases 5/6: annotations, trained release and Self-RAG runtime | Pending; no trained adapter or model-serving claim |
| Phase 6K: managed Kaggle | Pending platform, security and trained-runtime gates |
| Phase 8: combined research evaluation | Pending; no benchmark or training-cost results |

Implemented components are in `backend/documind`, the native migration in `migrations/versions`, test fixtures/cases in `backend/tests`, and local setup/smoke tools in `scripts`. [Local development](docs/LOCAL_DEVELOPMENT.md) lists actual routes and commands. Qdrant is provisioned but no search index is built. Search/inference remain disabled. OCR is opt-in; default Compose stays native-only.

## Phase 1B and review-fix verification — 2026-11-08

The full local suite passed **47 tests**, with zero skips and five PyMuPDF/SWIG dependency warnings, using the locked OCR extra and actual downloaded/hashed model artifacts. The three real OCR tests skip explicitly unless DOCUMIND_TEST_OCR_ROOT is set. Ruff passes. Final Docker rebuild and upgrade to 0003_table_types passed; Alembic reports no new operations. The expanded real HTTP/Celery smoke passed native text/table preview → correction/review → pinned old-version read → deletion. Compose request logging was observed with its returned request ID. Both smoke fixtures (including one interrupted by container recreation) were verified synthetic and cleaned. [Runtime environment, checks and source hashes](docs/validation/phase1b-2026-11-08.json) preserve this evidence.

Implemented/covered: both text-read deletion races; full element pagination; emitted redacted request records under real Uvicorn; native tables across all four PDF rotations; empty cells/merged native headers/adjacent text; actual OCR of clean scans, scanned table, mixed/rotated PDF and API/job/review flow; manual correction with old-version resolution; invalid parent/null/array table provenance; missing OCR models; legacy reprocess fingerprints; consistent lease/deadline clock checks.

A read-only independent review found table-adjacent text loss, merged-span errors and legacy idempotency drift during implementation; these were fixed and independently checked. It also found PostgreSQL's NULL CHECK loophole for array coordinates; the object/type guard and regression now pass. Its final bounded recheck passed six targeted cases with no remaining actionable blockers in that scope. The suite exposed a host/DB clock mismatch; recovery, heartbeat and queued writes now use the same application clock, with an expired-heartbeat regression. These are correctness discoveries, not benchmark improvements.

Actual OCR diagnostics: [two clean synthetic scan metrics](docs/validation/phase1b-ocr-2026-11-08.json) record recognized text, whitespace-normalized CER/WER, elapsed time, actual environment and model revisions/file hashes. Both tiny cases had zero observed CER/WER; this is not representative accuracy or confidence calibration. First initialization took about 61 seconds; subsequent recorded cases were about 2.6/1.6 seconds on the development Mac, not the proposed host.

The optional OCR Compose overlay passes configuration/volume/platform merge checks only; its runtime was not executed. A package-download timeout during the native rebuild was resolved by increasing the download timeout and caching dependencies. Fresh copied-source startup was not repeated in this task.

## Historical correctness review — 2026-11-08

[Original findings and reproductions](docs/validation/phase01-review-2026-11-08.md). F1–F3 are resolved with permanent tests in test_review_regressions.py: deletion after initial access checks, element cursor identity and effective request logging. The original 28-test suite passed while missing these defects; retain the review as a lesson, not an open blocker.

## Initial build verification — 2026-11-08

Evidence: [environment, package versions and source/config hashes](docs/validation/phase01-2026-11-08.json).

| Check | Observed result |
|---|---|
| `uv run pytest -q` | 28 passed, 0 failed, 6 third-party deprecation warnings; macOS arm64 / Python 3.12.13 |
| `uv run ruff check backend scripts migrations` | Passed |
| `uv run alembic check` | No new upgrade operations |
| Migration upgrade/downgrade/upgrade | Passed in a disposable PostgreSQL database |
| Full Docker API/dispatcher/worker smoke | Passed upload → real Redis/Celery → PostgreSQL → text/PNG/original reads → revocation/cleanup; synthetic fixture deleted |
| Clean copied-source startup | Passed with new private config, empty DB/volumes and ephemeral ports; disposable resources removed |
| Remote CI | Workflow added; execution not observed |

Coverage includes rotated/image-only/encrypted/corrupt PDFs and limits, workspace isolation and scoped references, concurrent duplicate requests, immutable promoted sources, retry/lease recovery after a killed process, stale workers, reprocessing/preview races, deletion and orphan collection. This is native-slice coverage; it does not establish full-product acceptance scenarios or model behavior. Current process/service status must be checked when needed; the earlier running containers are not a permanent fact.

## Pending gates and limitations

- General layout/Docling, unruled/ragged/merged raster tables, automatic multi-page table linking and additional upload formats remain unimplemented. Existing text/header review cannot add missing cells or edit geometry.
- Local single-workspace Bearer access is not public/multi-user authentication. Public deployment, load/security evaluation and hosted storage remain unverified.
- Source storage is the private filesystem adapter (ADR-022), not a tested S3 service. Host and Compose object roots must not be mixed against the same populated database.
- English CPU OCR and bounded ruled-table extraction are available, always needs_review until explicit human confirmation. Confidence remains uncalibrated; OCR/table quality beyond clean synthetic fixtures is unverified. The inspection UI is API documentation, not Next.js.
- Model revisions, loaders, licenses/access, memory/latency and retrieval quality still require implementation-time validation. The proposed CPU host has no capacity benchmark.
- No trained Self-RAG release, inference connector or managed Kaggle deployment exists. Kaggle credentials being configured does not remove platform/runtime gates.
- Five dependency deprecation warnings remain in the latest suite; do not hide them. Remote CI and the optional OCR Docker runtime have not been executed locally.

## Next steps

The three review findings and bounded Phase 1B gates are implemented/tested. Phase 2 is the next planned milestone; use the recorded local OCR limits rather than assuming general document understanding.

When the user starts Phase 2, read the retrieval, ingestion/publication, data-model, evidence, inference, security and API contracts. Implement encoder-safe chunking and matching workspace-scoped Qdrant/BM25 publications, then RRF/reranking, bounded evidence-v1 answers and one supported custom-endpoint baseline adapter with real tests. Keep the existing native preview flow working; do not begin QLoRA or managed Kaggle as a dependency.

For startup/testing commands use [LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md). Initial configuration runs once and refuses to overwrite `.env`; do not reset volumes or rerun setup destructively to gather context.

## Condensed history

- **2026-11-07 — design audit:** revised token budgets, extraction/index readiness, durable dispatch/publication, evidence checks, scoped provenance, reflection protocol, query replay and independent release gates (ADRs 011–019). Documentation checks were completed; they did not implement services.
- **2026-11-08 — model/inference decisions:** selected EmbeddingGemma 2 / hybrid-v3 (ADR-020), then managed Kaggle/custom endpoints and immutable trained releases (ADR-021). Runtime gates remain pending.
- **2026-11-08 — native build:** implemented and locally verified Phase 0/1A (ADR-022). Durable debugging lessons are in MEMORY; the evidence artifact above preserves package versions and hashes. No Git clone/commit was claimed.
- **2026-11-08 — persistent context workflow:** established root AGENTS/MEMORY/PROGRESS/DECISIONS ownership under ADR-023. Previous status/decision paths are compatibility pointers. This was documentation maintenance, not a new application milestone or runtime test run.

## Historical context-maintenance verification — 2026-11-08

Verified the context links, Markdown structure, original ADR IDs, referenced source/test names and canonical status/decision ownership. The recorded native implementation artifact hashes still match the current files. No application code/configuration, credentials, model assets or validation artifact was changed, and no runtime tests were rerun for this documentation-only task. Old status narration was condensed; substantive decisions and debugging lessons were retained in their owners.

## Source-control publication — 2026-11-08

At the user's request, initialized Git on `main` and configured public remote [here-2007/DocuMind](https://github.com/here-2007/DocuMind). The remote was empty when checked; no existing history was replaced. The snapshot includes implementation, tests, migrations, architecture/contracts, persistent context, synthetic validation records and the owner-supplied reference PDF. Private configuration, local documents/derived assets, dependencies, caches and OCR model weights remain excluded.

Publication preparation verified that all 34 source/configuration hashes still match the recorded Phase 1B validation, so no application tests were rerun for this Git/documentation task. Initial implementation commit `82d4c327919b60593b1ce72a6c387b9b1609af97` was pushed successfully to `origin/main`; `git ls-remote` confirmed the remote branch matched that commit. Local main tracks origin/main. GitHub Actions was triggered by the push; no completed remote CI result is claimed here. The publication record is committed separately from the initial implementation snapshot.
