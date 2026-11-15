# Run DocuMind locally

## Local-only model setup

ADR-042 is the current execution contract. Run the application and backing stores on this device. Choose the fine-tuned reflection runtime and answer generator independently. Older operator probe sections below retain their diagnostic commands.

Install the local extras with `uv sync --frozen --extra retrieval --extra training --extra kaggle`. Start local backing services and provision retrieval/tokenizer models using the recipes below. On an existing host installation, run `uv run --no-sync alembic upgrade head` before restarting the API, dispatcher and worker on the updated code. The current head is `0010_artifacts_dispatch`. Keep the existing database and object root paired. The default API image remains suitable for native preview and comparison. Run model-enabled API and workers as host processes so loopback services refer to the same device.

The Linux app lock intentionally uses CPU PyTorch. For a local NVIDIA runtime, use a separate CUDA-enabled Python environment with the training dependencies as described in the Phase 5 runbook. The private Kaggle bootstrap uses its CUDA environment. CPU and MPS use the locked local environment.

The reviewed Qwen v4 release is opt-in. The validated reflection profile is `nf4-cuda` on the private Kaggle bridge. Baseline search, preview and comparison can run without the trained model. In acceptance on a 16 GB MacBook Air, the trained `fp16-mps` service passed readiness on retry but timed out during its first document query. The clean setup check used verified cached weights. [Release and setup evidence](validation/local-release-2026-11-15.json).

### Existing Qdrant installations

Fresh `scripts/init_local.py` configuration generates `QDRANT_API_KEY`. Compose requires this key and disables Qdrant CORS. For an older private `.env`, add only the missing key, then recreate only Qdrant with its existing volume:

```sh
uv run --no-sync python scripts/init_local.py --secure-qdrant
docker compose up -d --no-deps qdrant
```

The upgrade appends a generated key without changing existing settings and never prints it. Keep the same key in API, worker and dispatcher environments. Do not expose the Qdrant port beyond loopback. Indexing configuration rejects missing or short keys. Restart host processes after changing private settings.

Readiness and repository-backed API operations check the installed Alembic revisions against this source tree. `DATABASE_MIGRATION_REQUIRED` returns HTTP 503 until `uv run --no-sync alembic upgrade head` succeeds against the paired database. This check never migrates data automatically. `/health` remains process liveness.

### Fine-tuned model on this device

Choose a checked bundle and its independently recorded SHA. Use the actual corpus, recorded case review and generated evaluation for that bundle. For v4, the recorded review was completed by Codex with explicit delegation under ADR-044.

```sh
export DOCUMIND_RUNTIME_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run --no-sync python -m documind.reflection_runtime \
  --bundle "$bundle_dir" --expected-bundle-sha "$bundle_sha" \
  --corpus "$corpus_dir" --review "$review_file" \
  --generated-evaluation "$generated_evaluation_file" \
  --device checkpoint --host 127.0.0.1 --port 8010
```

An NF4 checkpoint requires an NVIDIA CUDA device with `--device checkpoint`. Explicit `--device cpu` or `mps` loads an FP32 CPU base or FP16 MPS base and the saved adapter. A 4B base needs roughly 16 GB in FP32 or 8 GB in FP16, plus adapter, application and runtime memory. Actual peak memory and speed require measurement on the device. The service reports its inference profile. Revalidate changed precision before accepting a release. For experimental compatibility checks add `--pilot`. Pilot mode remains unavailable to normal app admission.

Set these values privately and consistently for the local API, worker and dispatcher.

```dotenv
SELF_RAG_ENABLED=true
REFLECTION_LOCATION=local
SELF_RAG_RUNTIME_ORIGIN=http://127.0.0.1:8010
SELF_RAG_RUNTIME_TOKEN=<same private runtime token>
SELF_RAG_MANIFEST_SHA256=<checked reviewed bundle SHA>
SELF_RAG_INFERENCE_PROFILE=nf4-cuda
```

Match the profile to the chosen device. CPU uses `fp32-cpu` and MPS uses `fp16-mps`.

### Fine-tuned model on private Kaggle

The configured local Kaggle credentials authorize the operator command. Set `DOCUMIND_BRIDGE_TOKEN` to the private token that the local app will use. Keep the bridge command running while querying.

For the reviewed v4 bundle on this installation, use these private artifacts from the repository root. The independently recorded digest is also in the release evidence above.

```sh
bundle_dir=.data/phase6-adapter-v4-version2-output/qwen-adapter-v4
bundle_sha=0cfdee37208cb418e41727126882f73fcf1eb8812ee01c03dd19a24f161c2543
corpus_dir=.data/phase6-adapter-v4-corpus
review_file=.data/phase6-validation-2026-11-15/recorded-agent-review.json
generated_evaluation_file=.data/phase6-validation-2026-11-15/v4-test-reviewed-evaluation.json
```

```sh
export DOCUMIND_BRIDGE_TOKEN="$DOCUMIND_RUNTIME_TOKEN"
uv run --no-sync python scripts/run_kaggle_model.py \
  --role reflection --owner "$KAGGLE_USERNAME" \
  --state .data/local-kaggle-reflection/session.json --port 8010 \
  --bundle "$bundle_dir" --manifest-sha "$bundle_sha" \
  --corpus "$corpus_dir" --review "$review_file" \
  --generated-evaluation "$generated_evaluation_file"
```

Use `REFLECTION_LOCATION=kaggle` and the same loopback origin/token in the local app configuration. The bridge keeps the upstream Jupyter URL private. It admits only the selected inference routes. Ctrl-C stops the bridge and requests exact-session cancellation. The original 20-minute expiry and detached watcher also apply. Use the operator status/stop commands below to reconcile retained state after interruption. An explicit `--pilot` is available for experimental bridge probes.

### Answer generation on this device

Use a running OpenAI-compatible local generator, or the bundled service. Select a model and pinned revision that fit this device.

```sh
export DOCUMIND_GENERATOR_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run --no-sync python -m documind.generator_runtime \
  --model HuggingFaceTB/SmolLM2-135M-Instruct \
  --revision 12fd25f77366fa6b3b4b768ec3050bf629380bac \
  --device auto --port 8011
```

The small model exercises operation. Choose a suitable generator for answer quality. In the local API/worker settings, allowlist the exact `LOCAL_CHAT_ENDPOINT=http://127.0.0.1:8011/v1/chat/completions`. Add and validate that URL in Inference connections with its model ID and `DOCUMIND_GENERATOR_TOKEN`. Ollama may use `http://127.0.0.1:11434/v1/chat/completions` with no provider key when that exact URL is allowlisted. Ollama serves generation, while the custom fine-tuned reflection service owns trained labels.

### Answer generation on private Kaggle

```sh
export DOCUMIND_BRIDGE_TOKEN="$DOCUMIND_GENERATOR_TOKEN"
uv run --no-sync python scripts/run_kaggle_model.py \
  --role generation --owner "$KAGGLE_USERNAME" \
  --state .data/local-kaggle-generator/session.json --port 8011 \
  --model Qwen/Qwen3-4B-Instruct-2507 \
  --revision cdbee75f17c01a7cc42f958dc650907174af0554
```

Add the bridge's loopback chat-completions URL as a generation connection with its bridge token. A separate reflection bridge can use port 8010 and its own state/token. Kaggle quota and concurrent allocations may limit simultaneous sessions.

### Answer generation through an external API

Add the provider's HTTPS chat-completions URL, model ID and API key in Inference connections. Keys are encrypted in the local database. DNS-pinned HTTPS, response limits and revocation checks apply. The workspace shows the evidence destination.

In chat, select **Trained reflection** and the desired generation connection. A ready fine-tuned runtime with candidate-scoring support is required. Selecting **Fine-tuned model also generates** retains the integrated path. Selecting **Baseline RAG** keeps baseline behavior. Native preview and deterministic comparison need no generator.


The local app supports PDF extraction, deterministic invoice/PO comparison and optional hybrid search, grounded QA and custom chat endpoints. Backend requirements are Docker Compose, `uv`, and Python 3.12. The Next.js workspace also requires Node.js 22 or later and npm. Run backend commands from the repository root. Native text, ruled tables and comparisons require no GPU, Kaggle credentials or model weights. OCR and search require the separately provisioned CPU profiles below.

## Start

```sh
uv sync --frozen --python 3.12
uv run python scripts/init_local.py
docker compose up -d --build
uv run python scripts/smoke.py
```

`init_local.py` creates a mode-0600 `.env` with random local credentials; it refuses to overwrite an existing file. On subsequent starts, omit that command. Keep `.env` private. A real local `.env` was created during development and is excluded from version control and image builds.

Compose starts PostgreSQL, Redis, Qdrant, an Alembic/init one-shot service, FastAPI, a Celery worker and the durable-job dispatcher. The default image supports native extraction. Phase 2 uses the host-process recipe below with the same types of backing services. Only API and development infrastructure ports are published, all on `127.0.0.1`. API, worker and dispatcher share a persistent private object-store volume; no object-storage service exposes files directly. The backend is **local-only**, even though its container listens internally on all interfaces for Docker port forwarding. Keep this installation private.

Open [API documentation](http://127.0.0.1:8000/docs). All data routes require the local Bearer token from your private `.env`; the documentation's Authorize button accepts it. The schema and health route contain no document data. The synthetic smoke client reads configuration privately, submits a two-page native/table PDF, polls its job, checks text/image/table reads, creates a corrected review version, checks the pinned old version, then deletes its fixture. It never prints credentials.

The sole authorized workspace defaults to `3e34d85d-2ebe-4141-8024-cbfe5feec000`. Changing `LOCAL_WORKSPACE_ID` explicitly selects another local workspace; this is not public account management. Never use request headers supplied by a browser to select an arbitrary principal.

## Implemented routes

All paths below are relative to `/api/v1`.

| Method | Path | Behavior |
|---|---|---|
| GET | `/health` | Process liveness |
| GET | `/ready` | Configured workspace initialization, store, broker, extraction and search/inference readiness |
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
| GET | `/workspaces/{workspace_id}/cleanup-jobs` | Pending or failed deletion jobs available for recovery |
| POST | `/workspaces/{workspace_id}/jobs/{job_id}/retry` | Explicit idempotent retry of failed work |

Every work-creating POST requires `Idempotency-Key` (1–128 printable ASCII characters, no spaces). Same key/body replays existing work; conflicting reuse returns 409. DELETE is idempotent per document. Use new keys for deliberate new uploads. Reprocessing uses the server's current parser/config identity; unchanged configuration reuses the matching job/version. Retry a failed job explicitly with a new retry key. Replaying an old retry key never resets a later failure again.

Page/image/element reads accept `ingestion_version`; omitted values use the current preview pointer. `page_number` is 1-based, and a page response includes its pinned extraction's `page_count`. Bounding boxes use normalized top-left coordinates relative to the rendered, rotated page. Element character offsets index its stored page text. Preserve `text_raw` for source quotes. The Next.js workspace and FastAPI documentation both provide source inspection.

## Tests and migrations

```sh
docker compose up -d --wait postgres redis qdrant
uv run ruff check backend scripts migrations
uv run pytest -q
uv run alembic check
```

Tests create and drop a unique `documind_test_<uuid>` database, use temporary private object storage, and isolate their Celery queue and Qdrant collection. Integration tests need the local services and private configuration. Unit-only execution is `uv run pytest -q -m 'not integration'`. The historical Phase 1B audit reproductions now have permanent regressions. The latest checks are in [project progress](../PROGRESS.md).

The suite includes migration upgrade/downgrade/upgrade and schema comparison in an empty disposable database. Apply application upgrades with the one-shot Compose migration service or `uv run alembic upgrade head` for host development. Future schema changes must use new Alembic revisions. Downgrade is for disposable databases because older revisions can drop data. Phase 2 refuses downgrade when its data is populated.

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

For macOS host workers, add `--pool=solo` to the Celery command to use the worker configuration exercised in local acceptance.

Host processes use `.data/objects`; Compose overrides the root to `/objects` on its named volume. Do not mix the two object roots against the same populated database: use one deployment consistently, or deliberately migrate stored objects before switching. The integration database is isolated from both. Native source metadata lives in PostgreSQL, so restoring either storage independently without matching metadata is not a complete backup.

## Recovery and limits

The DB job row is durable; the dispatcher repeatedly sends due IDs and requeues expired leases. Workers fence every promotion by lease token and expiry. PDF parsing runs in a killed-on-timeout subprocess, with page/pixel/text/render-byte limits. Docker limits the worker's memory and process count. Pages/elements are promoted in one transaction; no partial staged output is served. Without OCR, image/low-text pages require review; with OCR, real recognized text/confidence is retained but remains `needs_review`. Native/scanned table output also requires review. Corrupt/encrypted/oversized input produces explicit errors. Failed reprocessing leaves the old immutable preview available.

Originals are stored atomically before upload commit. Parser output files are immutable, version-addressed objects; a repeated write must match existing bytes. The dispatcher collects unreferenced artifacts older than the configured grace period, preserving active jobs and committed source references. It also collects artifacts left by cancelled workers after deletion. This local adapter uses private filesystem permissions and a Docker volume; S3 is a later adapter under ADR-022.

`/ready` checks DB/schema, private-store access, Redis and enabled capability configuration. Use the model probe and job polling to check actual model execution and processing. The dispatcher retries a broker outage and job lease recovery fences stale workers. Finite retry budgets end in a failed job. Deletion immediately blocks source reads, cancels active ingestion, revokes affected search publications and schedules retriable physical cleanup. Non-content tombstones remain.

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

It retains the same DB/object volumes, mounts the verified model root read-only, installs the locked OCR extra, sets a 180-second parser deadline and gives the single worker a 2-GB memory limit. The model directory must already exist. PaddlePaddle has no selected Linux ARM wheel; the overlay selects linux/amd64. Its merged configuration was checked, but the OCR Docker runtime was not executed on this ARM development machine. Default native Compose was rebuilt and smoke-tested separately; GitHub CI passed, while target-host capacity remains unverified. Returning to native mode uses the base compose.yaml alone. Do not change to host object storage against the same populated DB.

All OCR/table output remains preview-only needs_review until POST /review confirms every page, optionally correcting existing element text or table-cell is_header. Use a source base_ingestion_version and a new Idempotency-Key. Review jobs return the normal upload-style summary; poll the job, then read the new version. Old version URLs remain valid until deletion. No geometry/missing-cell editor, automatic multi-page table linking, unruled scan-table reconstruction or calibrated confidence is claimed. See API_CONTRACT.md and INGESTION.md for the exact boundary.

PDF extraction starts in a subprocess with a fixed environment and private working directory. On macOS it applies a Seatbelt sandbox before opening the PDF. On Linux it requires kernel Landlock ABI 3 (Linux 6.2 or newer with Landlock enabled) and `libseccomp.so.2` (Debian/Ubuntu package `libseccomp2`). The default Docker base image already supplies the library. A host or container that cannot apply the sandbox returns `PARSER_SANDBOX_UNAVAILABLE`. Keep the worker’s memory and process limits in Compose. On macOS the parent samples parser resident memory and terminates it above 1.5 GiB. On Linux the parser caps virtual address space at 1.5 GiB for native parsing and 6 GiB for OCR library reservations.

Already promoted sources remain readable after upgrade. A queued job pinned to an unavailable old parser fails with `PARSER_VERSION_UNAVAILABLE`. Explicitly reprocess with the installed parser/config.

## Phase 2 hybrid search and baseline QA

Start PostgreSQL, Redis and Qdrant with `docker compose up -d --wait postgres redis qdrant`. Install and probe the pinned CPU models:

```sh
uv sync --locked --extra retrieval
uv run --no-sync python scripts/setup_search.py --probe
```

Add `--extra ocr` to the sync command if using OCR in the same worker. The setup script downloads the pinned EmbeddingGemma 2 and MiniLM revisions into the local Hugging Face cache. The worker loads cached weights, so use the same user and cache for both commands.

For a fresh host deployment, set `INDEXING_ENABLED=true` in your private settings and use the host-process startup above with `uv run --no-sync` so it retains the installed extras. For a database already paired with Compose's object volume, use a separate development database, `CELERY_QUEUE`, `OBJECT_STORE_ROOT` and `QDRANT_COLLECTION` for the host processes. Set the same values in the API, worker and dispatcher environments, run `uv run --no-sync alembic upgrade head` and `uv run --no-sync python -m documind.cli init` against that database, and choose a free API port. For local model execution start Celery with `--pool=solo --concurrency=1`. This keeps model work in one process.

In the authenticated API documentation, upload a PDF with a supported field such as `Grand total: INR 1,100.00`. Use a fresh `Idempotency-Key` for each new operation. Wait for a complete extraction and confirm review if required. All routes below use the prefix `/api/v1/workspaces/{workspace_id}`.

1. `POST /publications` with no body. Poll `GET /jobs/{job_id}` for success and `GET /publications/{publication_id}` for the published snapshot. Publish again after adding or reprocessing sources to make the new versions searchable.
2. `POST /queries` with `{"question":"What is the invoice grand total?","document_ids":["<document UUID>"],"mode":"baseline_rag"}`. Poll `GET /queries/{query_id}` using the response's `query_run_id`. Use `GET /queries/{query_id}/events?after_seq=0` for the trace and `GET /citations/{citation_id}` for the raw source range. Use `POST /queries/{query_id}/cancel` to cancel a queued or running query.
3. For optional model generation, set `GENERATION_ENABLED=true` in the private process settings. Fresh setup generates `INFERENCE_SECRET_KEY`. On existing installs, generate a Fernet key and save it privately under that setting, preserving it across restarts. Create a connection with `POST /inference-connections` using `display_name`, `endpoint_url`, `model_id` and the write-only `api_key`. An operator can allow one exact loopback URL with `LOCAL_CHAT_ENDPOINT`. Public endpoints use HTTPS on port 443. Run `POST /inference-connections/{id}/validate`, wait for the job, then confirm the connection is `ready` and include its ID as `connection_id` in the query. The validation probe is synthetic. Queries send only the question and verified excerpt after checking that the run can still use that source and connection.

The development evaluation uses clean synthetic invoices. For the full local test run, provision OCR as above and cache the real loopback test model separately:

```sh
uv run --no-sync python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    "HuggingFaceTB/SmolLM2-135M-Instruct",
    revision="12fd25f77366fa6b3b4b768ec3050bf629380bac",
    allow_patterns=["config.json", "generation_config.json", "model.safetensors", "tokenizer*"],
)
PY
DOCUMIND_TEST_OCR_ROOT="$PWD/.data/models/ocr" DOCUMIND_TEST_RETRIEVAL=1 \
  DOCUMIND_TEST_CHAT_MODEL=1 DOCUMIND_TEST_EVAL=1 uv run --no-sync pytest -q
```

The loopback test starts its own server and uses synthetic input. Leave the test flags unset for the default suite, which skips model-dependent checks. To write a fresh development evaluation, run `DOCUMIND_TEST_RETRIEVAL=1 DOCUMIND_TEST_EVAL=1 DOCUMIND_EVAL_OUTPUT=.data/phase2-development.json uv run --no-sync pytest -q -k development_evaluation`. See [the recorded evaluation](validation/phase2-development-2026-11-08.json) and [the API contract](API_CONTRACT.md#3-query-creation-and-result) for details.

## Phase 3 workspace

Start the backend API, worker and dispatcher using one consistent deployment from the recipes above. Apply migrations through `0006_comparisons`. For chat, provision the retrieval models and enable indexing with the host retrieval recipe. The default backend Docker image supports native extraction and comparison.

Create a private `frontend/.env.local` using `frontend/.env.example`. Set `DOCUMIND_API_TOKEN` to the backend's `LOCAL_API_TOKEN` and set `DOCUMIND_API_URL` to its loopback URL, such as `http://127.0.0.1:8000`. These are server settings. Preserve an existing private configuration.

```sh
cd frontend
npm ci
npm run dev
```

Open [the local workspace](http://127.0.0.1:3000). The web server binds to loopback and forwards protected data requests to the API with the server-held token. A production web build can be run with `npm run build` followed by `npm start`.

1. Choose a PDF and upload it. The workspace shows upload progress and the extraction job state. Open a document for page navigation and its original download. Review all pages before explicitly confirming any extraction that needs review.
2. Publish the search index and wait for the publication to finish. Searchability is displayed separately from extraction status. Select published documents for chat.
3. Ask a supported question such as `What is the invoice grand total?`. Open its citation to see the pinned page, exact excerpt and highlighted source element. Unsupported questions show insufficient evidence. The trace displays persisted retrieval and verification events.
4. For generation, configure a custom endpoint in Inference connections, then validate it and explicitly select its ready configuration in chat. The destination is displayed before submission. Editing or revoking a connection invalidates the selection. Self-RAG and managed Kaggle remain unavailable.
5. Cancel a running query from the chat. A disconnected trace reconnects by sequence without creating a second query. If retries are exhausted, Resume trace subscribes to the same run.

To verify the complete slice after installing the OCR and retrieval extras and caching the real test model described above:

```sh
cd frontend
npm ci
npm run typecheck
npm run build
npx playwright install chromium
cd ..
DOCUMIND_TEST_OCR_ROOT="$PWD/.data/models/ocr" DOCUMIND_TEST_RETRIEVAL=1 \
  DOCUMIND_TEST_CHAT_MODEL=1 DOCUMIND_TEST_EVAL=1 DOCUMIND_TEST_BROWSER=1 \
  uv run --no-sync pytest -q
```

The browser harness starts its own API, Next.js server, Celery worker and dispatcher with disposable backing data and ephemeral ports. It uses actual PDF extraction and retrieval, plus a synthetic local completion endpoint. Separate backend tests load a real SmolLM2 model. The browser test covers trace disconnection and duplicate delivery, citation navigation, cancellation, connection replacement/revocation and visible failure states. The live SSE test covers heartbeats, disconnect without cancellation, and source revocation. The Celery recovery test kills a worker after retrieval starts and replays the completed run after recovery.

## Phase 4 deterministic comparison

Start the backend and workspace above. Save each block as a separate PDF with selectable text and upload both. Wait for extraction to complete. For documents flagged for review, inspect the pages and confirm the corrected extraction before creating the investigation.

```text
Document type: Invoice
Invoice number: INV-001
PO reference: PO-0100
Vendor ID: VEN-005
Grand total: INR 1,100.00
Grand total basis: tax-inclusive
```

```text
Document type: Purchase order
PO number: PO-0100
Vendor ID: VEN-005
Grand total: INR 1,000.00
Grand total basis: tax-inclusive
```

1. Open **Compare documents**, select both PDFs and choose **Run comparison**. The sources can be unpublished.
2. Expect **Grand totals differ** and an exact difference of **INR 100.00**. Open each source to see its quote and highlight.
3. Choose **Prepare CSV report**, then **Download CSV report**. The two rows include both source anchors and the same inputs and difference.

The Phase 7 CSV workflow works with indexing and all inference disabled. If submission is interrupted, retry with the same selected PDFs to recover the accepted investigation. Use **Open previous investigation** to reopen saved results and **Refresh** to recheck the open sources. The upload defaults are 20 MiB per PDF and 200 documents per workspace. Set `MAX_UPLOAD_BYTES` or `MAX_WORKSPACE_DOCUMENTS` in the backend configuration to change them. Open an unused document and choose **Delete PDF** to free a slot. Uploads can use the slot once deletion cleanup succeeds. Earlier reports referencing the deleted document become unavailable as soon as deletion is accepted.

If deletion cleanup exhausts its automatic attempts, restore the unavailable dependency and use **Retry cleanup** in Documents. Pending and failed cleanup IDs persist across browser reloads. A lower upload limit applies to new PDFs and preserves accepted retries and reads of existing originals. If source verification fails, restore the corresponding original or preview objects from the matching private backup.

For API clients, the [investigation routes](API_CONTRACT.md#5-investigations) provide the same flow. Use a fresh idempotency key for each distinct operation and poll `/jobs/{job_id}` after run or report creation. A new investigation pins the currently selected versions. The deterministic rule requires explicit IDs, common currency and common tax basis. Missing or ambiguous fields produce a needs-review finding. Run just the rule and PostgreSQL tests with `uv run --no-sync pytest -q backend/tests/test_comparison.py`.

After building the frontend and installing Chromium as above, run the comparison-only browser acceptance with:

```sh
DOCUMIND_TEST_BROWSER=1 uv run --no-sync pytest -q \
  'backend/tests/test_phase3_browser.py::test_next_workspace_in_browser[phase7]'
```

## Phase 5 offline reflection training

The trainer runs separately from the application. It reads only the supplied corpus. The pilot uses project-authored fixtures and the tracked [frozen manifest](../backend/tests/fixtures/phase5_reflection_manifest.json).

```sh
uv sync --frozen --inexact --extra training
uv run --no-sync python -m documind.reflection_pilot .data/reflection-pilot
cmp backend/tests/fixtures/phase5_reflection_manifest.json .data/reflection-pilot/manifest.json
reflection_sha=$(shasum -a 256 backend/tests/fixtures/phase5_reflection_manifest.json | cut -d ' ' -f 1)
uv run --no-sync python scripts/train_reflection.py --mode smoke \
  --corpus .data/reflection-pilot --expected-corpus-sha "$reflection_sha" \
  --epochs 6 --max-steps 120 --learning-rate .003 --evaluate-test \
  --output .data/reflection-smoke
```

Choose fresh output directories. The CPU smoke downloads the pinned open SmolLM2 checkpoint on first use. It records optimizer losses and supervised tokens per second, checks changes and reload identity for all 15 input and output rows, and records a greedy decision. Inspect `manifest.json`, `metrics.json` and `predictions.jsonl`. Initial retrieval matrices report raw model choices and family-constrained predictions separately. Omit `--evaluate-test` during tuning to score validation only.

Export manual-review cases after selecting a bundle on validation, scoring the frozen test split and saving its actual greedy evaluation. Set `bundle_dir`, `corpus_dir` and `bundle_sha` to that selected bundle, frozen corpus and independently recorded manifest digest. Set `generated_evaluation_file` to its test greedy JSON. The v3 evaluator below produces that artifact; the CPU smoke's conditional label file alone cannot supply it.

```sh
uv run --no-sync python -m documind.reflection_review \
  --bundle "$bundle_dir" --corpus "$corpus_dir" \
  --expected-bundle-sha "$bundle_sha" \
  --generated-evaluation "$generated_evaluation_file" \
  --output .data/reflection-review-packet.json
```

The packet includes questions, pinned evidence spans, gold targets, conditional label predictions and actual generated answers, critic labels, finish reasons and recomputed outcomes. Its generation method is explicit. Deduplicated or final episodes omitted by the diagnostic have `generation_status: not_evaluated`; no generated output is invented for them. This diagnostic does not exercise the application trajectory.

Review at least 20 unique cases across both retrieval policies. Save a JSON object with `bundle_manifest_sha256`, `generated_evaluation_sha256` copied from the packet, `reviewer`, and `cases`. Each case has `sample_id`, `verdict` (`accept` or `reject`) and a written `note`. An accepted false-full-support case is invalid. Rerun the export command with the same generated artifact, `--review <submission.json>` and a fresh output filename to record the review. Pass that recorded output to the runtime. It includes the corpus digest, derived counts and experimental status, all revalidated at startup. Raw submissions and older gold-only reviews cannot satisfy the new gate. Store these files privately when they include licensed or authorized non-public data.

For `--mode qlora`, provision a separate Linux Python 3.12 environment with NVIDIA CUDA and a CUDA-enabled PyTorch, then install `.[training]` via pip. The repository's Linux uv lock selects the CPU PyTorch wheel for app development. The target uses the pinned open Qwen3-4B-Instruct-2507 revision from ADR-031. `--mode qlora-smoke` exercises NF4 with the open smoke model on CUDA. To train beyond the pilot, supply the independently frozen manifest SHA of an adjudicated corpus and review the per-case outputs. The trainer writes experimental bundles. Release review and the Phase 6 runtime are separate gates.

For the human pass, inspect each original PDF/question, recorded application answer, citation page/excerpt, actual retrieval decision and critic labels against the evidence that episode saw. Write an accept/reject verdict and your own reason for at least 20 distinct frozen sample IDs, covering required and eligible retrieval. Keep uncertain cases pending rather than guessing. Gold annotation targets and conditional predictions are separate from actual generated outputs; unrun episodes remain unrun. Paired synthetic cases reuse invoice IDs with changed values in different PDFs, so judge the linked source for each case independently.

Review the known long-tail/math misses as supplementary application cases alongside the formal set. An abstention is correct when a field is missing or contradictory; abstaining on an answerable task remains an incomplete outcome. Supplementary IDs outside the frozen corpus are not accepted by the formal submission schema. If the disposable test DB has been dropped, use retained original PDFs and recorded excerpts for independent source checks; API citation resolution was measured at execution time. Completed case notes alone do not approve release or erase known failures. Current packet location and human completion status are tracked in PROGRESS.md.

For the independent v3 diagnostic, export the new frozen corpus and train using its recorded digest. Keep test scoring disabled during checkpoint selection.

```sh
uv run --no-sync python -m documind.reflection_corpus_v3 .data/reflection-v3
cmp backend/tests/fixtures/phase6_reflection_corpus_v3_manifest.json .data/reflection-v3/manifest.json
corpus_sha=$(shasum -a 256 backend/tests/fixtures/phase6_reflection_corpus_v3_manifest.json | cut -d ' ' -f 1)
uv run --no-sync python scripts/train_reflection.py --mode qlora \
  --corpus .data/reflection-v3 --expected-corpus-sha "$corpus_sha" \
  --epochs 3 --batch-size 4 --learning-rate .0005 --output "$bundle_dir"
uv run --no-sync python scripts/evaluate_reflection_v3.py \
  --bundle "$bundle_dir" --expected-bundle-sha "$bundle_sha" \
  --corpus .data/reflection-v3 --split validation --output .data/v3-validation.json
```

The new training policy masks unsupported answer words while retaining their critic labels. The greedy evaluator checks generated values against visible evidence and counts false full-support claims. After validation selection, evaluate the frozen test split once with the same saved weights and the same evaluator version for the previous adapter. Preserve the corpus, manifests, raw outputs, hardware and evaluator digest. Human release review follows separately.

For v4, export with `python -m documind.reflection_corpus_v4` and compare its manifest with [the frozen v4 manifest](../backend/tests/fixtures/phase6_reflection_corpus_v4_manifest.json). Use that digest in the same QLoRA command and `evaluate_reflection_v3.py --split validation`. V4 has 160 training, 40 validation and 60 held-out cases. The addition operands stay in separate unordered-pair groups across splits. The reviewed addition cases remain outside the corpus as application regressions.

For a selected bundle that contains validation-only scores, collect conditional test predictions without changing its manifest or weights. Run the target NF4 profile on CUDA:

```sh
uv run --no-sync python scripts/evaluate_reflection_v3.py --conditional \
  --bundle "$bundle_dir" --expected-bundle-sha "$bundle_sha" \
  --corpus "$corpus_dir" --split test --output .data/selected-test-conditional.json
```

Keep the original test greedy output unchanged. In a fresh copy, embed the conditional JSON under `conditional_evaluation` after checking that `bundle_manifest_sha256`, `corpus_manifest_sha256` and `split` agree. Supply that combined file as `--generated-evaluation` to the review tool and runtime. Its digest binds both actual generations and complete conditional predictions. The packet rejects conflicting bundled test scores, wrong bindings and missing/malformed held-out rows. [ADR-043](../DECISIONS.md#adr-043-score-selected-checkpoints-without-changing-their-immutable-bundles).

Human adjudication remains distinct from explicitly delegated operator review. For the November 15 authorization, Codex records its own agent identity under [ADR-044](../DECISIONS.md#adr-044-identify-delegated-agent-review-honestly); these verdicts are not human research evidence. The earlier human v3 verdicts do not approve v4.

## Phase 6 private reflection runtime

The model process uses the training extra. Supply a separate private `DOCUMIND_RUNTIME_TOKEN` of at least 24 characters through the process environment. Start a trained CPU pilot bundle using its independently recorded manifest digest.

```sh
uv run --no-sync python -m documind.reflection_runtime --pilot \
  --bundle .data/reflection-smoke --expected-bundle-sha "$bundle_sha" \
  --host 127.0.0.1 --port 8010
```

Inspect the model service in another terminal that has access to the same private token.

```sh
curl -fsS -H "Authorization: Bearer $DOCUMIND_RUNTIME_TOKEN" \
  http://127.0.0.1:8010/runtime/v1/capabilities
```

The pilot advertises `reflection_pilot`. To verify the full local model transport test, set `DOCUMIND_TEST_REFLECTION_BUNDLE` to the FP32 pilot directory and run `uv run --no-sync pytest -q backend/tests/test_reflection_runtime.py`. Set `DOCUMIND_TEST_RETRIEVAL=1` with the provisioned search models to exercise the real workspace retriever with a scripted protocol responder.

A reviewed runtime requires the pinned Qwen NF4 bundle, a corpus beyond the original synthetic pilot, and a completed accepted case review. Human review and explicitly delegated agent review have distinct provenance; ADR-044 permits the latter for this local experimental validation. Launch without `--pilot` and supply `--corpus`, `--review` (the recorded output) and `--generated-evaluation` as well as the selected bundle SHA. The runtime verifies the artifact hash, frozen test coverage, actual outcomes, review bindings and derived counts, and runs a model probe before reporting readiness. This does not certify a representative research release.

Generate the expanded synthetic corpus using `uv run --no-sync python -m documind.reflection_corpus .data/reflection-expanded` and compare the resulting manifest with [the frozen manifest](../backend/tests/fixtures/phase6_reflection_corpus_manifest.json). Use that digest with `--mode qlora`. Its cases are synthetic gold labels. Human acceptance of the selected checkpoint is recorded separately using the Phase 5 review flow.

Upgrade application metadata to the current head. Run `uv run --no-sync python scripts/setup_search.py --reflection-tokenizer` with the retrieval extra to cache the exact Qwen tokenizer on the app/worker host. This step downloads tokenizer files. Ordinary baseline setup remains independent. The app verifies the token map, vocabulary, native special IDs and chat-template digest against the runtime, then counts full serialized input and reserved output. A missing or different tokenizer yields `RUNTIME_TOKENIZER_NOT_READY`. Upgrade app and runtime together for the required `chat_template_sha256` capability.

In the API and worker environment set `SELF_RAG_ENABLED=true`, `SELF_RAG_RUNTIME_ORIGIN` to the loopback origin of the runtime or private Kaggle bridge, `SELF_RAG_RUNTIME_TOKEN` to its private token, and `SELF_RAG_MANIFEST_SHA256` to the approved digest. Supply the origin without a path or trailing slash. An approved runtime makes `/api/v1/ready` report `self_rag: true`. Select trained Self-RAG in the workspace or use `mode: self_rag` in the query API. Select the appropriate source documents for document questions.

A replacement worker replays confirmed checkpoints and ordered passage IDs within the original deadline and budget. Unconfirmed work fails closed.

From a trusted GPU environment, compare the service with direct inference on the same bundle. Supply `DOCUMIND_RUNTIME_TOKEN` privately and run the probe against the approved loopback origin.

```sh
uv run --no-sync python -m documind.runtime_probe \
  --origin "$runtime_origin" --bundle "$bundle_dir" --expected-bundle-sha "$bundle_sha" \
  --corpus "$corpus_dir" --review "$review_file" \
  --generated-evaluation "$generated_evaluation_file"
```

The probe checks exact identities, token sequences and outcomes across two decision prompts and one evidence candidate. Reflection probabilities must agree within absolute tolerance 0.001. An explicit `--pilot` validates an experimental bundle in a trusted local environment.

### Selected-model application measurement

`scripts/evaluate_selected_workflow.py` explicitly allocates a private Kaggle session and uses disposable application stores. V4 freezes 73 synthetic PDF/general cases before inference. It retains PDFs, actual model steps, failures, citations and source hashes. The experimental harness preserves `reflection_pilot` and injects admission/transport only in that disposable environment; this is separate from normal reviewed-runtime admission.

```sh
uv run --no-sync python scripts/evaluate_selected_workflow.py \
  --bundle "$bundle_dir" --expected-bundle-sha "$bundle_sha" --corpus "$corpus_dir" \
  --owner "$kaggle_owner" --state .data/selected-session.json \
  --output .data/selected-workflow.json
```

For split evaluation, start the independently pinned generator first. Set `DOCUMIND_SELECTED_GENERATOR_TOKEN` privately and add `--generator-endpoint`, `--generator-model` and `--generator-revision` (40-character pinned revision). The actual local generation connection must validate; its exact version and returned candidates enter the application path. `--generated-evaluation-output` additionally saves test greedy episodes using the same loaded target runtime. Choose fresh output/state paths and retain failed runs.

On a constrained MPS device, load retrieval weights before calibrating generator latency. A synthetic warm-up outside user-query deadlines may be necessary after another model load. Keep model quality runs separate from the full test suite. Neither calibration nor retry may reset an uncertain user-query outcome or raise the application's bounded inference timeout. Current measured outcomes, review provenance and remaining coverage limits are in [PROGRESS.md](../PROGRESS.md).

## Phase 6K private Kaggle serving probe

The operator tool uses the configured Kaggle account and the official private Jupyter session API. Set `kaggle_owner` to that account's username. Install its optional dependencies without removing the other local extras.

```sh
uv sync --frozen --inexact --extra kaggle
uv run --no-sync python scripts/kaggle_serving_probe.py run \
  --owner "$kaggle_owner" --state .data/kaggle-private/session.json \
  --bundle "$bundle_dir" --manifest-sha "$bundle_sha" \
  --output .data/kaggle-private/result.json
```

Use the independently recorded manifest digest of a checked experimental bundle. The run creates a unique private notebook, verifies ownership and source, starts one GPU session, uploads the bundle and allowlisted runtime modules, and runs the synthetic HTTP probes through Kaggle's authenticated Jupyter gateway. It verifies runtime authentication and duplicate-step replay. A persisted 20-minute expiry includes allocation, installation, model loading and serving. A detached local watcher cancels that exact session at expiry even if the operator command exits. A separate remote guardian kills the complete model subprocess group at its deadline; the notebook kernel holds no model weights. The child receives its bearer token through its environment, not its saved program.

The whole Jupyter gateway URL is private because its path contains a credential. Keep the owner-only session state until cleanup is reconciled. Stop checks the configured owner and the recorded session/creation-operation binding; editing or deleting the notebook cannot prevent cancellation of that old session. Start/poll still reject changed templates. The notebook source, result JSON and console messages contain no gateway URL or runtime token. The command uses `reflection_pilot` and cannot approve the model or activate application Self-RAG.

```sh
uv run --no-sync python scripts/kaggle_serving_probe.py status \
  --state .data/kaggle-private/session.json
uv run --no-sync python scripts/kaggle_serving_probe.py stop \
  --state .data/kaggle-private/session.json
```

A repeated start using the same state cannot allocate another session after an uncertain create. `stop_requested` means Kaggle accepted the exact-session cancellation. `access_revoked` requires an observed denial from the private gateway. Network failure leaves cleanup unconfirmed. Check the owned session in Kaggle when the available API cannot establish that its GPU allocation ended. Managed user credentials, browser Start/Stop/Revoke controls and approved-release admission remain later Phase 6K work.

At expiry, the watcher retries transient SDK request failures up to twice against the same recorded session. The waits are two and five seconds. If all attempts fail, it records the last error for reconciliation. The model deadline and session ownership checks still apply during cleanup.

Creation itself is inside cleanup. The operation response is persisted before metadata validation, and a failed run writes a private failure/cleanup result. Status after expiry attempts cleanup before notebook-source checks. A saved original deadline is never extended by retry. The local watcher needs the operator host to remain available; after a host restart, run status/stop using the retained state. The remote process deadline still bounds the model but does not independently prove platform GPU release.

For a retained operation receipt, `reconcile` validates it and requests cancellation without creating another session. If the response was completely lost, independently identify the original operation and its exact unique notebook in Kaggle first. This is an explicit operator attestation; the API checks operation identity but does not independently supply the notebook linkage. Never guess a session ID or adopt an unrelated notebook.

```sh
uv run --no-sync python scripts/kaggle_serving_probe.py reconcile \
  --state .data/kaggle-private/session.json
# Only when supplying an independently confirmed missing receipt:
uv run --no-sync python scripts/kaggle_serving_probe.py reconcile \
  --state .data/kaggle-private/session.json \
  --operation "$creation_operation" --confirm-kernel "$created_kernel"
```

## Selected-adapter PDF-to-answer evaluation

Install the locked OCR/retrieval/training/Kaggle extras and provision local PostgreSQL, Redis, Qdrant and search models. Use the selected frozen corpus/bundle and a fresh output/state path. The evaluator uses disposable application stores, actual PDF APIs/jobs/retrieval/verification and real private GPU steps. Only experimental admission and gateway transport are injected; production Self-RAG admission and the browser connection workflow remain unchanged. Preserve the real pilot capability and synthetic-only data boundary.

```sh
uv run --no-sync python scripts/evaluate_selected_workflow.py \
  --bundle "$bundle_dir" --expected-bundle-sha "$bundle_sha" \
  --corpus "$corpus_dir" --owner "$KAGGLE_USERNAME" \
  --state .data/selected-evaluation/session.json \
  --output .data/selected-evaluation/result.json
```

The tool freezes 36 original v3 test cases plus ten explicit edge/general cases. With the v4 corpus it freezes 60 fresh test cases, the same ten regression cases and three fresh long-invoice cases. Repeat `--case <id>` for a labeled diagnostic subset. It retains original PDFs and raw contexts/results privately, tests citation resolution before dropping the disposable DB and records independent expiry cancellation and cleanup status. The original v3 run and limitations are in [the validation artifact](validation/selected-pdf-workflow-2026-11-09.json).

CI installs the training extra, generates the committed pilot corpus and trains a fresh CPU bundle with a 120-step cap. `DOCUMIND_TEST_REFLECTION_BUNDLE` selects it and `DOCUMIND_REQUIRE_REFLECTION_BUNDLE=1` forbids an optional skip. Reproduce the workflow locally with a fresh bundle directory and the Phase 5 commands above. The CPU check establishes train/save/reload/authenticated serving, not Qwen quality or GPU capacity.

## Phase 8 frozen benchmark

Use the existing PostgreSQL, Redis and Qdrant services, the locked Python extras, pinned retrieval artifacts, and the checked Qwen v4 bundle. The runner creates a disposable database, object root and Qdrant collection. It uploads only the synthetic evidence excerpts to a private authenticated Kaggle runtime. `phase8_gpu.py` loads base-only and trained NF4 on the same GPU. The retained state and receipt are private and expire with that session.

```bash
mkdir -p .data/phase8
chmod 700 .data/phase8
uv run --no-sync python scripts/phase8_gpu.py \
  --allocate-for-owner "$KAGGLE_USERNAME" \
  --state .data/phase8/session-1.json --receipt .data/phase8/receipt-1.json \
  --bundle "$DOCUMIND_REFLECTION_BUNDLE" --manifest-sha "$DOCUMIND_REFLECTION_MANIFEST_SHA"
HF_HUB_OFFLINE=1 uv run --no-sync python scripts/evaluate_phase8.py \
  --manifest-sha256 6bf099104bb6211a35f5f30497a0f0b4c5c53a8e3e50851f7c76ebe73e443c3b \
  --state .data/phase8/session-1.json --receipt .data/phase8/receipt-1.json \
  --output .data/phase8/shard-1
uv run --no-sync python scripts/kaggle_serving_probe.py stop --state .data/phase8/session-1.json
```

Use the bundle path and SHA-256 from the existing checked v4 release when supplying the variables in this example. The benchmark reserves time for a case before the session expires. If cases remain, deploy to a fresh state/receipt and pass `--resume-from .data/phase8/shard-1` plus any other existing shards. Keep each run directory. When using several shards, record the hardware and runtime identity of each. The validator rejects duplicate cases and mismatched profiles. Omit `--state` and `--receipt` to run retrieval-only metrics. `--group` selects whole groups for a smoke run.

An evaluation shard may finish successfully at the session boundary with cases still pending. The final report lists every missing case and exits with code 2 until the frozen cohort is complete. Code 0 from the report requires complete case coverage.

```bash
uv run --no-sync python scripts/phase8_report.py \
  --manifest evaluation/datasets/phase8-v1.jsonl \
  --run .data/phase8/shard-1 .data/phase8/shard-2 \
  --output .data/phase8/report --review-samples 20
uv run --no-sync python scripts/phase8_documents.py \
  --ocr-root .data/models/ocr --output .data/phase8/documents
```

To reproduce the older cohort, generate a separate manifest with `scripts/phase8_dataset.py --pretraining-corpus <verified-v4-corpus> --output <new-file>`. Pass that path and its SHA-256 to the evaluator and output a separate report. Keep model startup, query times and cleanup evidence in their own records. The established watcher bounds each Kaggle session to 20 minutes. A cancellation receipt or revoked gateway cannot independently prove physical GPU deallocation.

New reports check the source snapshot against the checkout that ran the evaluation. If that checkout has since changed, use `--source-root` with the retained, independently identified checkout. Model runs and retrieval-only runs produce distinct reports. Use `--historical` to rescore only the published November 15 archives with matching raw and config hashes. The three original broader GPU shards each recorded a distinct code identity and produce separate reports.

For blinded review, open `blind-review.json` beside the run's `pdfs/` directory. Review both anonymous answers for exactness, safe abstention and whether the cited source page supports the answer. Keep `blind-review-key.json` closed until the independent verdicts are recorded. Both files are derived from actual paired outputs. Generating a packet records zero human verdicts.
