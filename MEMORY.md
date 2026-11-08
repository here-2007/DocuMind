# Persistent project knowledge

Durable facts and lessons for future Codex sessions. [AGENTS.md](AGENTS.md) owns instructions, [PROGRESS.md](PROGRESS.md) owns current status/evidence, and [DECISIONS.md](DECISIONS.md) owns decisions. Update this file only when knowledge changes; validate time-sensitive facts against source and the environment.

## Product and boundaries

- DocuMind is a business-document intelligence platform. The original [Self-RAG PDF](reference/original_self_rag_spec.pdf) supplies the research baseline; its GATE/UPSC corpus, Streamlit UI, estimates and example resume metrics are not current product requirements or measured results. [Source deviations](docs/CHANGES_FROM_PDF.md) are authoritative.
- The implemented slice is PDF ingestion/preview, optional English CPU OCR, ruled tables and immutable human review. The full architecture is a staged design, not a list of deployed capabilities. See PROGRESS before choosing work.
- Initial operation is local-only: one configured workspace and private Bearer token, with Compose host ports on loopback. This is not public or multi-user authentication.
- A source quote must resolve to stored raw text and immutable document/version/page/element anchors. PDF pages are 1-based; boxes use normalized top-left coordinates on the rotated rendered page. Native confidence is null, not certainty.
- `Decimal`/rules must own arithmetic. Future document-specific queries must retrieve authorized evidence; documents never supply instructions or tool permissions.

## Source and environment map

| Area | Owner / entry point |
|---|---|
| Local setup, routes and recovery | [LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md) |
| FastAPI and access boundary | [api.py](backend/documind/api.py) |
| Transactions, requests, versions, leases and cleanup | [repository.py](backend/documind/repository.py) |
| PDF parsing, OCR, tables and review | [parser.py](backend/documind/parser.py), [ocr.py](backend/documind/ocr.py), [tables.py](backend/documind/tables.py), [review.py](backend/documind/review.py) |
| Private atomic filesystem objects | [storage.py](backend/documind/storage.py) |
| Celery delivery / DB dispatch | [worker.py](backend/documind/worker.py), [cli.py](backend/documind/cli.py) |
| Schema and migrations | [schema.py](backend/documind/schema.py), [0001_native.py](migrations/versions/0001_native.py) |
| Config, dependency lock and local containers | [config.py](backend/documind/config.py), [pyproject.toml](pyproject.toml), [uv.lock](uv.lock), [compose.yaml](compose.yaml) |

- The current package requires Python 3.12; use `uv sync --frozen --python 3.12`. Runtime settings reject enabling unimplemented indexing/generation/Self-RAG/Kaggle flags. Blank future model settings do not establish a model loader.
- Configuration initialization creates a private `.env` and refuses overwrite. Never read/print its secrets for context gathering or copy credentials into memory, logs, examples or reports.
- Docker object data is on the `objects` volume at `/objects`; host development uses `.data/objects`. Do not use these different roots interchangeably against a populated database. Durable backup needs both metadata and objects.
- The MinIO image pull failed during the initial build; ADR-022 chose the permitted private filesystem equivalent. There is no tested S3 adapter.
- Tests create a unique PostgreSQL database, object root and queue namespace. They require real local PostgreSQL/Redis; they do not substitute SQLite for PostgreSQL constraints. Avoid using the demo queue for tests.
- Kaggle authentication was reported configured by the user. Use existing configured credentials only for authorized Kaggle work; availability does not prove GPU quota, model rights or platform permission. No Kaggle secret belongs in this file.
- Git was initialized on `main` on 2026-11-08 for the user-requested publication through Phase 1B. `origin` is https://github.com/here-2007/DocuMind.git (public; empty when first checked). Check current Git/runtime state before assuming a commit, clean checkout or running service. Private `.env`, `.data` (including model weights), `.venv` and caches are ignored.

## Model and inference commitments (planned)

- ADR-020 selects text-only `google/embeddinggemma-2`, normalized 768-dimensional vectors. The retained reranker is `cross-encoder/ms-marco-MiniLM-L6-v2`; controlled generation uses `meta-llama/Llama-3.2-3B-Instruct`. No weights or model-quality result are established by those selections.
- [RETRIEVAL.md](docs/RETRIEVAL.md) owns `hybrid-v3`, tokenizers, prompts and budgets. Its 240/64 chunks are measured with the reranker tokenizer; a larger Gemma context does not remove the reranker's pair limit. Model/prompt changes require isolated encoding/index identities.
- [INFERENCE.md](docs/INFERENCE.md) defines managed user Kaggle and custom endpoints. Ordinary external endpoints offer baseline RAG without another local Llama. A complete, compatible trained runtime may offer Self-RAG; an adapter alone is not a generator or judge.
- A trained release is fixed until deliberately retrained/versioned. Uploads and queries update evidence/context, not weights. Save the base revision, adapter, tokenizer/token map, trained token rows and protocol/runtime manifest together. No trained release exists in the recorded implementation.
- Connection/mode switches are explicit new runs. No silent provider fallback, data forwarding or downgrade of Self-RAG. Kaggle management remains gated by platform permission, secure credentials, lifecycle operations and runtime tests.
- The proposed 2-vCPU/16-GB app host has no measured model-serving capacity. CPU embedding/reranking and offloaded generation are a deployment target, not a throughput guarantee.

## Lessons that must survive implementation changes

| Discovery | Consequence / evidence |
|---|---|
| Generated unique-constraint names collided when based only on the first column | Schema names include all constrained columns; verify migrations against real PostgreSQL. |
| The demo worker could consume integration jobs sharing its queue | Isolate database, object root **and** Celery queue/key prefix; see [test fixtures](backend/tests/conftest.py). |
| Deletion invalidates the active ingestion lease before promotion | A stale worker may stop with `LEASE_LOST`; API reads must still fail closed. |
| An older extraction can finish after a newer preview | Guard preview-pointer promotion; preserve the newest applicable preview. See `test_old_completion_cannot_replace_newer_preview`. |
| Failed reprocessing must not erase the last usable source | Keep immutable versions and a separate preview pointer; tested by `test_reprocessing_failure_preserves_previous_preview`. |
| Broker submission is not durable application state | DB jobs, leases, fencing and redispatch own recovery; do not claim exactly-once physical execution. |
| Checking visibility once before reading text leaves a deletion race | Recheck source visibility before returning content; the 2026-11-08 review reproduced a page response after deletion committed before its SELECT. Both routes are now covered by `test_deletion_during_text_read_revokes_response`. |
| Document and element rows carry different cursor identities | Encode the same primary key used by the query's ordering/filter, and test full pagination, not just the first page. |
| Calling `logger.info` does not establish observable request logging | Test the effective logger level/handler under actual Uvicorn startup; its default configuration suppressed DocuMind request records in the review. |
| Valid citation identity is not substantive support | The planned evidence-v1 gate must independently validate claims; ranking scores and critic labels cannot certify arbitrary prose. |

Test names above are in [test_ingestion.py](backend/tests/test_ingestion.py). Preserve meaningful regression coverage when those implementations change; remove or revise a lesson only when its underlying constraint changes.

The three Phase 0/1A review defects are fixed with regressions in test_review_regressions.py. PROGRESS.md owns current validation.

## Phase 1B operating constraints

- ADR-024 selects PaddleOCR 3.7.0 / PaddlePaddle 3.3.1 CPU, PP-OCRv5 mobile detector and English recognizer. Provision once with scripts/setup_ocr.py; inference requires verified local artifacts and never substitutes native text for requested but unavailable OCR. Config and page provenance retain model revisions/file hashes. OCR is disabled in the default native-only Compose deployment.
- Native ruled tables use PyMuPDF; complete raster grids use OpenCV plus actual OCR. Preserve empty cells, spans, parent IDs, coordinates and header uncertainty. Do not claim general layout, automatic cross-page linking or calibrated confidence.
- All OCR/table extraction requires human review. The review route confirms all pages and optionally corrects existing text/header flags; it creates a new immutable version through the durable job path. It cannot add missing cells, repair geometry or make empty scans successful. Earlier source versions remain available until deletion.
- Table deduplication must work at word/span boundaries: filtering whole native blocks lost adjacent text. Merged cells need actual spans and row delimiters. Both discoveries have regressions in test_extraction.py.
- Preserve existing idempotency payloads across upgrades; adding a null field changes request fingerprints. Use one clock consistently for writing and selecting local job deadlines; mixing host writes with PostgreSQL time caused a reproduced lease-recovery failure.
- PostgreSQL CHECK accepts SQL NULL: JSON structures require object/type guards in addition to required-key and numeric comparisons. Migrations 0002/0003 add table provenance and reject invalid/null/array coordinates.
