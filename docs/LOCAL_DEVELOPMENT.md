# Run PDF extraction locally

Phase 0/1A plus bounded Phase 1B implement a local single-workspace backend. Requirements: Docker Compose, `uv`, and Python 3.12 (uv can install it). Run commands from the repository root. Native text/ruled tables require no GPU, Kaggle credentials or model weights. OCR is optional and requires the separately provisioned CPU profile below.

## Start

```sh
uv sync --frozen --python 3.12
uv run python scripts/init_local.py
docker compose up -d --build
uv run python scripts/smoke.py
```

`init_local.py` creates a mode-0600 `.env` with random local credentials; it refuses to overwrite an existing file. On subsequent starts, omit that command. Keep `.env` private. A real local `.env` was created during development and is excluded from version control and image builds.

Compose starts PostgreSQL, Redis, Qdrant (unused until Phase 2), an Alembic/init one-shot service, FastAPI, a Celery worker and the durable-job dispatcher. Only API and development infrastructure ports are published, all on `127.0.0.1`. API, worker and dispatcher share a persistent private object-store volume; no object-storage service exposes files directly. The backend is **local-only**, even though its container listens internally on all interfaces for Docker port forwarding. Do not expose this installation to the internet; public user authentication is not implemented.

Open [API documentation](http://127.0.0.1:8000/docs). All data routes require the local Bearer token from your private `.env`; the documentation's Authorize button accepts it. The schema and health route contain no document data. The synthetic smoke client reads configuration privately, submits a two-page native/table PDF, polls its job, checks text/image/table reads, creates a corrected review version, checks the pinned old version, then deletes its fixture. It never prints credentials.

The sole authorized workspace defaults to `3e34d85d-2ebe-4141-8024-cbfe5feec000`. Changing `LOCAL_WORKSPACE_ID` explicitly selects another local workspace; this is not public account management. Never use request headers supplied by a browser to select an arbitrary principal.

## Implemented routes

All paths below are relative to `/api/v1`.

| Method | Path | Behavior |
|---|---|---|
| GET | `/health` | Process liveness |
| GET | `/ready` | DB/store/broker and enabled OCR artifact readiness; tables/review flags; search/inference false |
| GET | `/workspaces` | Configured local workspace |
| POST / GET | `/workspaces/{workspace_id}/documents` | Bounded PDF upload / paginated metadata |
| GET / DELETE | `/workspaces/{workspace_id}/documents/{document_id}` | Metadata / immediate access revocation plus cleanup job |
| POST | `/workspaces/{workspace_id}/documents/{document_id}/reprocess` | New parser/config version, or reuse existing matching work |
| POST | `/workspaces/{workspace_id}/documents/{document_id}/review` | Confirm all pages; optional existing text/header corrections create a new immutable extraction job |
| GET | `/workspaces/{workspace_id}/documents/{document_id}/original` | Authorized original PDF download |
| GET | `/workspaces/{workspace_id}/documents/{document_id}/pages/{page_number}` | Page text, geometry and image URL |
| GET | `/workspaces/{workspace_id}/documents/{document_id}/pages/{page_number}/image` | Authorized PNG preview |
| GET | `/workspaces/{workspace_id}/documents/{document_id}/elements` | Paginated paragraph/table/table_cell records, geometry, provenance and raw ranges |
| GET | `/workspaces/{workspace_id}/jobs/{job_id}` | Durable state, attempts and sanitized error |
| POST | `/workspaces/{workspace_id}/jobs/{job_id}/retry` | Explicit idempotent retry of failed work |

Every work-creating POST requires `Idempotency-Key` (1–128 printable ASCII characters, no spaces). Same key/body replays existing work; conflicting reuse returns 409. DELETE is idempotent per document. Use new keys for deliberate new uploads. Reprocessing uses the server's current parser/config identity; unchanged configuration reuses the matching job/version. Retry a failed job explicitly with a new retry key. Replaying an old retry key never resets a later failure again.

Page/image/element reads accept `ingestion_version`; omitted values use the current preview pointer. `page_number` is 1-based, bounding boxes use normalized top-left coordinates relative to the rendered, rotated page, and element character offsets index its stored page text. Preserve `text_raw` for source quotes; normalized text is separate. Native confidence is null. The basic inspection UI is FastAPI's API documentation; the Next.js viewer is Phase 3.

## Tests and migrations

```sh
docker compose up -d --wait postgres redis qdrant
uv run ruff check backend scripts migrations
uv run pytest -q
uv run alembic check
```

Tests create/drop a unique `documind_test_<uuid>` database using the local development PostgreSQL role and temporary object storage and a unique Celery queue namespace. They do not truncate the application database. Integration tests require the local services and private configuration; unit-only execution is `uv run pytest -q -m 'not integration'`. Fixtures are synthetic PDFs generated by test code, including rotated, encrypted and image-only cases. CI runs the same local-service tests on Ubuntu; the remote CI run has not been observed.

The suite includes migration upgrade/downgrade/upgrade in an empty disposable database. Apply application upgrades with the one-shot Compose migration service or `uv run alembic upgrade head` for host development. Future schema changes must use new Alembic revisions. Do not run downgrade on a database with documents: downgrading this first migration drops its tables.

## Running backend processes on the host

Use this alternative for fast editing; do not run it alongside the Compose API/worker/dispatcher using the same queue.

```sh
docker compose up -d --wait postgres redis qdrant
uv run alembic upgrade head
uv run python -m documind.cli init
uv run uvicorn documind.api:app --host 127.0.0.1 --port 8000 --no-access-log --limit-concurrency 4
```

In two additional terminals:

```sh
uv run celery -A documind.worker:celery worker --concurrency=1 --loglevel=WARNING
uv run python -m documind.cli dispatch
```

Host processes use `.data/objects`; Compose overrides the root to `/objects` on its named volume. Do not mix the two object roots against the same populated database: use one deployment consistently, or deliberately migrate stored objects before switching. The integration database is isolated from both. Native source metadata lives in PostgreSQL, so restoring either storage independently without matching metadata is not a complete backup.

## Recovery and limits

The DB job row is durable; the dispatcher repeatedly sends due IDs and requeues expired leases. Workers fence every promotion by lease token and expiry. PDF parsing runs in a killed-on-timeout subprocess, with page/pixel/text/render-byte limits. Docker limits the worker's memory and process count. Pages/elements are promoted in one transaction; no partial staged output is served. Without OCR, image/low-text pages require review; with OCR, real recognized text/confidence is retained but remains `needs_review`. Native/scanned table output also requires review. Corrupt/encrypted/oversized input produces explicit errors. Failed reprocessing leaves the old immutable preview available.

Originals are stored atomically before upload commit. Parser output files are immutable, version-addressed objects; a repeated write must match existing bytes. The dispatcher collects unreferenced artifacts older than the configured grace period, preserving active jobs and committed source references. It also collects artifacts left by cancelled workers after deletion. This local adapter uses private filesystem permissions and a Docker volume; S3 is a later adapter under ADR-022.

`/ready` checks DB/schema, private-store access, Redis and enabled OCR manifest/package availability, not loaded-model health, worker capacity or guaranteed completion latency. Job polling exposes actual processing/failure. The dispatcher retries a broker outage; no successful enqueue or exactly-once physical execution is assumed. Finite retry budgets end in a failed job that requires explicit retry. Deletion immediately blocks source reads, cancels active ingestion and schedules retriable physical cleanup; non-content tombstone IDs/status remain. Query APIs do not exist yet.

```sh
docker compose logs --tail=50 api worker dispatcher
uv run python -m documind.cli collect-orphans
docker compose down
```

`down` preserves named volumes. Avoid `down -v` unless intentionally discarding the local database, queue, originals and indexes. No durability claim is made for an ephemeral cloud-host filesystem; this configuration is local development, not a production deployment.

## Optional Phase 1B CPU OCR

Native tables are enabled by default; OCR stays disabled until explicitly selected. The implemented profile is English, PP-OCRv5 mobile detection/recognition, PaddleOCR 3.7.0 and PaddlePaddle 3.3.1. Install/provision once on a supported host (tested macOS arm64/Python 3.12):

```sh
uv sync --frozen --extra ocr
uv run --extra ocr python scripts/setup_ocr.py
DOCUMIND_TEST_OCR_ROOT="$PWD/.data/models/ocr" uv run --extra ocr pytest -q
uv run --extra ocr python scripts/evaluate_extraction.py --output .data/ocr-evaluation.json
```

The setup script refuses to overwrite an existing model root; omit it when already provisioned. It downloads public model files into ignored `.data/models/ocr`, records actual immutable repository revisions and SHA-256 hashes and verifies the profile. Keep the locked extra installed for OCR processes; native-only `uv sync` may remove optional dependencies. Do not commit weights. Inference validates these local files and never downloads/substitutes models. Missing or changed artifacts produce explicit availability/identity errors. OCR_LANGUAGE currently accepts en only. A separate root can be passed with `--root`; configure OCR_MODEL_ROOT to match.

To use OCR with an existing host-process deployment, set OCR_ENABLED=true and the absolute OCR_MODEL_ROOT privately for API/worker/dispatcher, keeping its database, object root and queue consistent. Restart those processes. The same PDF upload endpoint selects OCR per page; no generation key or Kaggle access is required. First model initialization took about a minute locally; later clean synthetic runs were faster. Parser timeout is explicit and results/latency depend on hardware.

An optional Linux x86_64 Docker recipe is provided:

```sh
docker compose -f compose.yaml -f compose.ocr.yaml up -d --build
```

It retains the same DB/object volumes, mounts the verified model root read-only, installs the locked OCR extra, sets a 180-second parser deadline and gives the single worker a 2-GB memory limit. The model directory must already exist. PaddlePaddle has no selected Linux ARM wheel; the overlay selects linux/amd64. Its merged configuration was checked, but the OCR Docker runtime was not executed on this ARM development machine. Default native Compose was rebuilt and smoke-tested separately; remote CI and target-host capacity remain unverified. Returning to native mode uses the base compose.yaml alone. Do not change to host object storage against the same populated DB.

All OCR/table output remains preview-only needs_review until POST /review confirms every page, optionally correcting existing element text or table-cell is_header. Use a source base_ingestion_version and a new Idempotency-Key. Review jobs return the normal upload-style summary; poll the job, then read the new version. Old version URLs remain valid until deletion. No geometry/missing-cell editor, automatic multi-page table linking, unruled scan-table reconstruction or calibrated confidence is claimed. See API_CONTRACT.md and INGESTION.md for the exact boundary.

Already promoted native-v1 sources remain readable after upgrade. A queued job pinned to an unavailable old parser fails with PARSER_VERSION_UNAVAILABLE; explicitly reprocess with the installed parser/config rather than silently substituting extraction behavior.
