# API contract - v1 target

This is the local-only v1 contract. It covers ingestion, publications, generation endpoints, trained reflection, citations, deterministic investigations and CSV reports. The [runbook](LOCAL_DEVELOPMENT.md) lists actual setup commands. The base path is `/api/v1`. FastAPI generates OpenAPI from Pydantic v2 schemas. Responses use `application/json` unless indicated.

## Shared rules

- IDs are UUID strings; all timestamps ISO 8601 UTC; `page_number` is **1-based**; money amounts serialized as **strings**, never binary floating point.
- User/session must be authorized for `workspace_id`, document IDs and query scope.
- Stable error payload: `{ "error": { "code": "...", "message": "...", "request_id": "...", "details": {} } }`.
- Every long-running task returns an ID and pollable status. Network errors/retries must not duplicate ingestion or findings.
- List routes use `limit` (default 50, maximum 200) and opaque cursor pagination.
- The backend returns **grounding status** and source references, not only a string answer.
- Request limits: file size, number of documents, context tokens and concurrency are configurable with documented defaults.

## 1. Health and workspace

`/ready` includes `database_schema_current`. An incompatible or uninitialized Alembic revision returns HTTP 503 with `DATABASE_MIGRATION_REQUIRED` and the upgrade command. Repository-backed routes enforce the same check before accessing application tables. Database connectivity failures are reported as `DATABASE_UNAVAILABLE` in readiness. No request performs an automatic migration; `/health` remains liveness.

| Verb | Route | Notes |
|---|---|---|
| `GET` | `/health` | Process liveness, no secrets |
| `GET` | `/ready` | Capability-aware dependency readiness; source reads stay independent of GPU/search |
| `GET` | `/workspaces` | Authorized workspace list |
| `POST` | `/workspaces` | Create workspace (when multi-workspace UI supported) |

Workspace capabilities require the configured workspace row. Native ingestion also needs the object store and dispatch. Search requires matching published indexes and ranking models. Generation requires an authorized ready connection with the requested capability. Self-RAG needs the admitted trained release and dispatch. OCR reports configured artifact availability independently. Page previews depend on the workspace and object store. Health and readiness expose no secrets. Connection details require owner/workspace authorization. Lifecycle states and enums come from [DATA_MODEL.md](DATA_MODEL.md).

### Inference connections (Phase 2 onward)

The local API implements versioned generation endpoint setup, validation and revocation. Local servers, private Kaggle bridges and remote providers share this shape. Kaggle model lifecycle uses the explicit local CLI. The application has one local principal and authorized workspace. Secret fields are write-only and encrypted locally.

| Verb | Route under /workspaces/{workspace_id} | Request and response |
|---|---|---|
| POST | /inference-connections | Idempotency-Key plus display_name, endpoint_url, model_id and optional api_key. Returns 201 configured metadata |
| GET | /inference-connections | Paginated sanitized metadata and tested generation capability |
| GET | /inference-connections/{id} | Sanitized metadata for the authorized connection |
| PATCH | /inference-connections/{id} | Idempotency-Key and expected_config_version. Creates a new version and invalidates readiness |
| POST | /inference-connections/{id}/validate | Synthetic probe job. Returns 202 and job_id |
| DELETE | /inference-connections/{id} | Revokes local dispatch and erases secrets. Returns 204 |

A private Kaggle model is started or stopped with its local bridge command and retained state. It exposes inference through authenticated loopback. Generation credential revocation and Kaggle allocation cleanup are separate operations. Query submission never provisions a notebook.

Idempotency uses protected keyed digests for secret-bearing bodies. The same key and body replay the accepted resource/version/job. A different body conflicts. Credential replacement or endpoint edits block new steps on the old version. A running query fails when its pinned version is no longer active.

## 2. Documents

| Verb | Route | Request / response |
|---|---|---|
| `POST` | `/workspaces/{workspace_id}/documents` | `multipart/form-data`, `file`, required `Idempotency-Key`; returns `202` with document/job IDs and extraction/index states |
| `GET` | `/workspaces/{workspace_id}/documents` | Paginated metadata with processing status |
| `GET` | `/workspaces/{workspace_id}/documents/{document_id}` | Metadata, latest/preview/published versions, extraction/index states, preview_available, searchable and error summaries |
| `GET` | `/workspaces/{workspace_id}/documents/{document_id}/pages/{page_number}` | Page dimensions and element anchors / thumbnail link |
| `GET` | `/workspaces/{workspace_id}/documents/{document_id}/pages/{page_number}/image` | Authenticated API-proxied rendered page image |
| `GET` | `/workspaces/{workspace_id}/documents/{document_id}/elements` | Filter by `page_number`, `element_type` |
| `GET` | `/workspaces/{workspace_id}/jobs/{job_id}` | Job kind/stage, status, attempts and error code |
| `GET` | `/workspaces/{workspace_id}/cleanup-jobs` | Paginated IDs, statuses and error codes for queued, running or failed deletions |
| `POST` | `/workspaces/{workspace_id}/jobs/{job_id}/retry` | Idempotent explicit retry for retryable non-query jobs; same logical resource/version, new lease attempts. Terminal query runs require a new query/key |
| `POST` | `/workspaces/{workspace_id}/documents/{document_id}/reprocess` | Required Idempotency-Key; changed parser/config creates immutable extraction work; matching identity reuses its job |
| `POST` | `/workspaces/{workspace_id}/documents/{document_id}/review` | Required Idempotency-Key; whole-document confirmation and optional existing text/header corrections create a new immutable reviewed extraction job (202) |
| `GET` | `/workspaces/{workspace_id}/documents/{document_id}/original` | Authorized API-proxied original PDF download |
| `DELETE` | `/workspaces/{workspace_id}/documents/{document_id}` | Begins safe source/index deletion, returns job ID |

Illustrative upload response:

```json
{"document_id":"<uuid>","job_id":"<uuid>","extraction_status":"queued","index_status":"not_started","preview_available":false,"searchable":false,"filename":"invoice.pdf"}
```

Page/image/element routes accept `ingestion_version`; omitting it uses the preview version. Citation links always specify their immutable version. Needs-review pages may be previewed with warnings. Delete atomically revokes visibility and creates cleanup work before returning 202; repeat deletes return the existing cleanup job. Lifecycle and recovery are specified in DATA_MODEL.md and INGESTION.md.

New uploads above `MAX_UPLOAD_BYTES` return `413 FILE_TOO_LARGE`. A full workspace returns `409 WORKSPACE_DOCUMENT_LIMIT` before writing an object or queuing work. `MAX_WORKSPACE_DOCUMENTS` defaults to 200 and counts queued, failed, ready and deleting documents until cleanup finishes. The same accepted upload replays at capacity or after the operator lowers the upload limit. The replay has a bounded body and must match the original receipt fingerprint.

Original downloads check the accepted SHA-256. Preview image reads verify saved render metadata and return `503 SOURCE_ARTIFACT_UNAVAILABLE` for missing files or `503 SOURCE_HASH_MISMATCH` for failed integrity checks. `preview_available` requires all preview files to exist and any recorded sizes to match. Reads recheck visibility before returning bytes. `/ready` reports `workspace_initialized`. An absent configured workspace blocks uploads and workspace discovery with `503 WORKSPACE_NOT_INITIALIZED` until initialization succeeds.

Phase 1B review request (local authenticated principal only):

```json
{"base_ingestion_version":"<64-character-sha256>","confirm_all_pages":true,"corrections":[{"element_id":"<uuid>","text_raw":"13.50","is_header":null}]}
```

Corrections are optional (default []), unique by existing non-table element ID in the pinned source version. Header flags apply only to table cells; empty replacement text is allowed only for cells. Unknown fields, invalid IDs/versions and conflicting idempotency reuse fail explicitly. Confirmation attests all pages were reviewed; it does not claim machine confidence calibration. Empty source pages fail review rather than become fabricated successful extraction. Review uses the normal durable job/status/preview response; previous versions remain readable. Text/header editing does not support adding missing elements or changing geometry.

The implemented element filter supports paragraph/table/table_cell; unknown types return 422. Filtering happens before pagination, whose cursor uses the ordered resource ID. Page metadata includes extraction warnings, DPI/model identity when OCR was used and reviewer/base provenance for reviewed versions. `/ready` reports configured OCR artifact availability plus tables/review capabilities; it does not load models or guarantee worker throughput. If OCR is enabled but its profile is missing, ingestion readiness fails and uploads return OCR_MODEL_UNAVAILABLE without creating a document.

Illustrative page element:

```json
{
  "element_id": "<uuid>",
  "page_number": 2,
  "element_type": "table_cell",
  "text": "INR 1,100.00",
  "bbox_norm": [0.72, 0.64, 0.91, 0.69],
  "source_type": "ocr",
  "confidence": 0.94,
  "table_coordinates": {"row": 7, "column": 4}
}
```

## 3. Query creation and result

Phase 2 adds `POST /workspaces/{workspace_id}/publications` with an idempotency key. It queues a replacement of the eligible workspace corpus and returns `publication_id` and `job_id`. Use the job and `GET /workspaces/{workspace_id}/publications/{publication_id}` to poll the build. The prior publication stays active until successful replacement.

The local custom adapter takes `display_name`, `endpoint_url`, `model_id`, optional `kind=custom_endpoint` and `protocol=openai_chat_completions`, and write-only `api_key`. PATCH takes the full new configuration and `expected_config_version`. DELETE immediately revokes and erases the local credential, returning 204. Query POST uses the documented 202 receipt. An omitted `connection_id` selects evidence-only baseline rendering. A selected connection enables the single chat step for verified fields. Optional `expected_config_version` on query creation rejects a stale connection selection. The Next.js workspace supplies this value. The events route supports JSON polling and SSE, with citation drilldown and immediate cancellation.


`POST /workspaces/{workspace_id}/queries` creates a durable run and always returns `202` for an accepted request. Require `Idempotency-Key`, scoped by principal, workspace and operation. Same key/body returns the existing run (including completed runs); same key with different canonical body returns `409 IDEMPOTENCY_CONFLICT`. Validate mode, authorization, document eligibility and request limits before accepting. Store request key, run and queued job in one transaction.

```json
{
  "question": "What is the total on invoice INV-100?",
  "document_ids": ["<invoice_uuid>"],
  "mode": "baseline_rag",
  "connection_id": "<connection_uuid>"
}
```

```json
{"query_run_id":"<uuid>","status":"queued","result_url":"/api/v1/workspaces/<workspace_uuid>/queries/<uuid>","events_url":"/api/v1/workspaces/<workspace_uuid>/queries/<uuid>/events"}
```

Request `mode` is `auto` or a result mode defined in DATA_MODEL.md. `auto` selects the baseline for document QA and deterministic comparison for supported calculations; it does not select research mode implicitly. Explicit unavailable `self_rag` returns `409 MODEL_NOT_READY`. The resolved mode and revisions are persisted, never relabeled after execution.

For model-backed queries, pin the configured local fine-tuned runtime and any selected generation connection independently. `mode=self_rag` with a `connection_id` uses split reflection and requires the runtime's `candidate_scoring` capability. The connection must be ready for chat generation, owned by the local workspace and match `expected_config_version`. Without a connection, the integrated trained checkpoint generates its own candidates. Ordinary endpoint output alone cannot enable Self-RAG. Document questions must supply selected `document_ids`. The bounded unscoped general grammar accepts addition or repeating a supplied reference and returns `unverified` without citations. Other unscoped Self-RAG questions return `SOURCE_SCOPE_REQUIRED`. Request bodies contain no API keys or raw endpoint overrides.

After an accepted run, upstream failures become the run's single persisted terminal error; result polling still returns 200 with failed status and sanitized error details. A switch to another endpoint/mode requires explicit user selection and a new request/key. Do not automatically retry uncertain generation, forward evidence to another provider, reset budgets or downgrade Self-RAG. Query cancellation also requests best-effort upstream cancellation; late results cannot change the terminal outcome.

For document questions, omitted `document_ids` resolves to the authorized searchable set once; an empty list is invalid, and selected unavailable documents return `409 DOCUMENT_NOT_READY`. No silent widening of scope. Record the publication and effective IDs at acceptance. General questions may have no publication/evidence and are `unverified` when answered without sources. Classifier uncertainty about private facts forces scoped evidence or abstention.

`GET /workspaces/{workspace_id}/queries/{query_run_id}` returns `200` with status while queued/running, or the persisted result/error when terminal. Final baseline result illustration (no measured latency implied):

```json
{
  "query_run_id":"<uuid>",
  "status":"succeeded",
  "mode":"baseline_rag",
  "query_type":"DOCUMENT_QA",
  "answer":"The invoice total is INR 1,100.00.",
  "grounding_status":"supported",
  "citations":[{"citation_id":"<uuid>","document_id":"<invoice_uuid>","ingestion_version":"<version>","page_number":1,"element_id":"<uuid>","chunk_id":"<uuid>","start_char":0,"end_char":25,"quote":"Grand total: INR 1,100.00","verification_status":"supported"}],
  "trace_id":"<uuid>",
  "publication_id":"<uuid>",
  "retrieval_calls":1,
  "inference":{"connection_id":"<connection_uuid>","config_version":1,"model_id":"Qwen/Qwen3-4B-Instruct-2507","model_revision":"<pinned_revision>","release_manifest_hash":null,"generation_used":true}
}
```

The result inference object is null when no generation was used. Provider-managed revision aliases must be labeled as such; no invented checkpoint hash. Traces and stored runs retain the admitted identity even on failure. The example above illustrates a configured baseline runtime, not a deployed model.

Trained results include `strategy` as `integrated_checkpoint` or `split_reflection`, the fine-tuned model/manifest/instance, actual inference profile and optional generator connection/version. `admitted_connection` pins the selected generator's capability check. The local generation connection projection supplies its configured and validated model identity. Readiness reports `app_location=local`, `reflection_location` and `split_reflection` availability.

Examples are illustrative; real offsets must be computed from exact stored text. Comparison results use `deterministic_comparison` and the rule in [EVIDENCE.md](EVIDENCE.md), with source-backed calculation records. `trace_id` equals `query_run_id` in v1. Internal claim/source checks and rejected candidates are retained with verifier revisions, but unauthorized content is never exposed.

## 4. Events, cancellation and source drilldown

There is one execution entry point. The read subscription `GET /workspaces/{workspace_id}/queries/{query_run_id}/events` serves SSE when `Accept` explicitly allows `text/event-stream` with a positive quality at least as high as JSON. Other requests receive paginated persisted JSON events.

```text
id: 1
event: trace_event
data: {"query_run_id":"<uuid>","seq":1,"type":"retrieval_started","data":{"requested_by":"policy"}}

id: 2
event: answer
data: {"query_run_id":"<uuid>","seq":2,"grounding_status":"insufficient_evidence","result_url":"/api/v1/workspaces/<workspace_uuid>/queries/<uuid>"}

```

Event IDs are decimal `seq`, monotonically increasing per run. Reconnect accepts `Last-Event-ID` or `after_seq` (both must agree if present), authorizes the run again, and replays strictly greater sequences. Invalid/future cursors return 400. Purged event history returns `410 EVENTS_EXPIRED` with `error.details.result_url` and a `Link` header for JSON and SSE requests. Clients deduplicate by `(query_run_id, seq)`. Wire delivery is at least once. DATA_MODEL.md enforces one durable terminal `answer` or `error` outcome. Reconnect after the terminal sequence returns 204. Clients reconnect after a disconnect before the terminal frame even if the query has already finished. Heartbeat comments are ephemeral.

Terminal frames include the result URL. Fetch answer text and citations through that authorized result route. A source revoked after the stream starts produces `event: stream_error` with `SOURCE_UNAVAILABLE`, then closes the subscription. This subscription error is transient and does not append another terminal event to the ledger. Source revocation before subscription returns HTTP 410.

Trace types: `policy_decision`, `generation_started`, `reflection_token`, `retrieval_started`, `retrieval_completed`, `passage_found`, `passage_filtered`, `evidence_windowed`, `identity_evidence`, `candidate_scored`, `citation_verified`, `warning`. `evidence_windowed` records the source ID and exact `start_char`/`end_char` selected for the generation context. `identity_evidence` records the field source ID, identity excerpt element ID and exact offsets. Terminal types: `answer`, `error`. Cancellation uses terminal `error` with `QUERY_CANCELLED`. Only trained model output produces `reflection_token`. Policy overrides are separate events. Trace payloads contain bounded metadata and source IDs. Raw source text and system prompts stay out of the trace.

Disconnecting a subscription does not cancel or restart work. `POST /workspaces/{workspace_id}/queries/{query_run_id}/cancel` idempotently sets cancellation intent, returns 202 for pending cancellation, or 200 with the existing terminal result. Workers check between stages and generation steps; cancellation/finalization contend on the same run row, so only one terminal transition wins. Enforce execution deadlines and lease recovery even when no browser is connected.

Retain event history for the configured period after termination; ongoing runs are never pruned. Result polling remains available until its retention/deletion policy removes it. Authorization/deletion is rechecked before each replay batch and response. If a source has been deleted, suppress historical answer/event content and return `410 SOURCE_UNAVAILABLE`; do not emit a second terminal event for an already finished run. Bytes already delivered cannot be recalled.

`GET /workspaces/{workspace_id}/citations/{citation_id}` resolves the pinned source version, page/element, raw quote and geometry after authorization. Browser navigation uses these stored values, never coordinates guessed by the model. A deleted source is unavailable, even if an old cached answer exists. Page rendering and downloads go through the authorized API in v1, avoiding stale public or signed links that bypass deletion checks.

Result, event and citation reads revalidate stored support against complete pinned sources. An older supported QA answer affected by a competing or incomplete table field returns `409 QUERY_RECHECK_REQUIRED`; create a fresh query to obtain the current abstention. The historical result is not rewritten. Baseline and Self-RAG share this rule.

Browser query recovery preserves the accepted-operation contract above: until a receipt is known, retain the original body and idempotency key, including source scope and destination/config version. A retry must recover the same query ID. Freeze editing until receipt or explicit choice to start a separate operation. Existing cached answers and source highlights clear on `QUERY_RECHECK_REQUIRED`, including after a backend verifier upgrade.

## 5. Investigations

Work-creating POSTs use the same scoped `Idempotency-Key` contract as query/upload creation. Creation pins two or three distinct authorized preview versions and returns `201` with `investigation_id`. The run and report requests atomically queue durable jobs and return `202` with `job_id`. Each investigation has one comparison and one CSV artifact. After a terminal job failure, start a new investigation with a new creation key. The first rule is `grand_total_match_v1`. [Evidence contract](EVIDENCE.md).

| Verb | Route | Notes |
|---|---|---|
| `POST` | `/workspaces/{workspace_id}/investigations` | Create investigation with source `document_ids` |
| `GET` | `/workspaces/{workspace_id}/investigations` | Paginated active investigation summaries |
| `POST` | `/workspaces/{workspace_id}/investigations/{id}/run` | Start deterministic extraction/link/validate task |
| `GET` | `/workspaces/{workspace_id}/investigations/{id}` | Status, rule version, linked records and findings |
| `GET` | `/workspaces/{workspace_id}/investigations/{id}/findings` | Computed discrepancies and evidence |
| `GET` | `/workspaces/{workspace_id}/investigations/{id}/evidence/{document_id}/{field}` | Pinned raw quote, geometry and authorized page URL |
| `POST` | `/workspaces/{workspace_id}/investigations/{id}/reports` | Queue a CSV from a finished investigation. Body `{"format":"csv"}` |
| `GET` | `/workspaces/{workspace_id}/reports/{report_id}` | Authorized report status, checksum and download URL when ready |
| `GET` | `/workspaces/{workspace_id}/reports/{report_id}/content` | Verified `text/csv` attachment |

Detail starts at `draft` and then follows the job status. A succeeded comparison can return a `needs_review` finding with `reason` and no verified difference. Money values are decimal strings. Source deletion returns `410 SOURCE_UNAVAILABLE` on old detail, evidence and report routes and removes the investigation from the list. The local `/queries` route continues to handle baseline QA. Comparisons use the investigation routes.

An investigation with a comparison label in a table cell yields `COMPARISON_TABLE_FIELDS_UNSUPPORTED`. An affected legacy finding, its evidence links, report creation or replay, ready report metadata and CSV download return `409 INVESTIGATION_RECHECK_REQUIRED`. Start a fresh investigation to produce the needs-review finding. Ready report metadata and downloads both revalidate the stored CSV against the current finding and checksum.

The workspace retries an unconfirmed comparison submission with the original source set and key. Once it receives the investigation ID, it keeps that ID across a failed detail read and resumes the same investigation. Refresh rechecks the open finding and clears its display, source highlight and download link when the verification gate rejects it.

Finding example:

```json
{
  "type": "AMOUNT_MISMATCH",
  "status": "verified",
  "mode": "deterministic_comparison",
  "retrieval": "structured_sql",
  "currency": "INR",
  "purchase_order_amount": "1000.00",
  "invoice_amount": "1100.00",
  "difference": "100.00",
  "rule_id": "grand_total_match_v1",
  "match_basis": {"method": "exact_po_and_vendor", "field": "po_number", "value": "PO-0100", "vendor_id": "VEN-005"},
  "evidence": [{"document_id":"<invoice_uuid>","page_number":1,"element_id":"<uuid>"}, {"document_id":"<po_uuid>","page_number":1,"element_id":"<uuid>"}]
}
```

## 6. Common HTTP errors

| Status | Code | Meaning |
|---|---|---|
| 400 | `INVALID_DOCUMENT` / `INVALID_QUERY` | Invalid input or unsupported format |
| 401/403 | `UNAUTHENTICATED` / `FORBIDDEN` | Auth required or workspace access denied |
| 404 | `NOT_FOUND` | Missing or unauthorized resource (avoid enumeration) |
| 409 | `MODEL_NOT_READY` / `CONNECTION_UNAVAILABLE` / `CONFIG_VERSION_CONFLICT` / `FEATURE_DISABLED` / `DOCUMENT_NOT_READY` / `IDEMPOTENCY_CONFLICT` | Unsupported mode, connection/configuration state, disabled gate, document readiness or idempotency conflict |
| 410 | `SOURCE_UNAVAILABLE` / `EVENTS_EXPIRED` | Deleted evidence or expired replay history |
| 413 | `FILE_TOO_LARGE` | Configured input size exceeded |
| 422 | `UNPROCESSABLE_DOCUMENT` / `RETRIEVAL_INPUT_TOO_LONG` | Insufficient document content or actual encoded input exceeds the configured budget |
| 429 | `RATE_LIMITED` / `INFERENCE_QUOTA_EXCEEDED` | App/provider rate or known GPU/account quota; no automatic provider switch |
| 500 | `INTERNAL_ERROR` | Unexpected failure with request ID; don't leak details |
| 502 | `INFERENCE_FAILED` / `INFERENCE_PROTOCOL_ERROR` | Upstream failed or response/release identity invalid |
| 503 | `DEPENDENCY_UNAVAILABLE` | Required dependency or matching index snapshot unavailable |
| 504 | `INFERENCE_TIMEOUT` / `QUERY_TIMEOUT` | Provider or query deadline exceeded. The dispatcher records `QUERY_TIMEOUT` even when a worker never claims the run |

## 7. Versioning and compatibility

Schema names, enum values and trace types are a proposed v1 starting contract. Once executable clients consume them, avoid unversioned breaking changes. Maintain OpenAPI snapshot tests and a typed frontend client generated from the actual FastAPI schema.

## Native slice implementation notes

Local Bearer access and configured workspace scope are implemented. Upload/retry/reprocess require Idempotency-Key. Lists use opaque UUID cursors and source reads honor extraction versions. Page reads include `page_count` for that pinned extraction. Native paragraph blocks expose raw/normalized text, page-text start/end ranges and rotated-page normalized boxes. Unsupported/corrupt/encrypted/limit failures use the stable error shape. The native worker completes or marks needs_review independently of indexing. Search readiness uses the same active artifact checks as retrieval. See ADR-022, ADR-026 and LOCAL_DEVELOPMENT for the implemented scope.
