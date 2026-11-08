# API contract - v1 target

This is the full v1 design contract. Phase 0/1A implements the local native subset listed in [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md); connection, query, investigation and report routes remain planned. Base path `/api/v1`. Implement in FastAPI with Pydantic v2 and generated OpenAPI. `application/json` unless indicated. Authentication for public releases is mandatory; a local development workspace is not authorization in production.

## Shared rules

- IDs are UUID strings; all timestamps ISO 8601 UTC; `page_number` is **1-based**; money amounts serialized as **strings**, never binary floating point.
- User/session must be authorized for `workspace_id`, document IDs and query scope.
- Stable error payload: `{ "error": { "code": "...", "message": "...", "request_id": "...", "details": {} } }`.
- Every long-running task returns an ID and pollable status. Network errors/retries must not duplicate ingestion or findings.
- List routes use `limit` (default 50, maximum 200) and opaque cursor pagination.
- The backend returns **grounding status** and source references, not only a string answer.
- Request limits: file size, number of documents, context tokens and concurrency are configurable with documented defaults.

## 1. Health and workspace

| Verb | Route | Notes |
|---|---|---|
| `GET` | `/health` | Process liveness, no secrets |
| `GET` | `/ready` | Capability-aware dependency readiness; source reads stay independent of GPU/search |
| `GET` | `/workspaces` | Authorized workspace list |
| `POST` | `/workspaces` | Create workspace (when multi-workspace UI supported) |

Readiness is capability-aware: native ingestion requires DB, object store and dispatch availability; search additionally requires matching published indexes and ranking models; generation requires an authorized ready connection with the requested capability; Self-RAG additionally requires the admitted trained release. Global health exposes only aggregate status; connection details require owner/workspace authorization. Return capability details without secrets. Do not make page previews depend on Qdrant/GPU availability. Lifecycle states/enums come from [DATA_MODEL.md](DATA_MODEL.md), not HTTP status codes.

### Inference connections (Phase 2 onward)

These routes are future contracts, not Phase 0/1A requirements. Every route requires the creating principal and workspace membership; workspace collaborators cannot use or administer another user's connection in v1. Secret request fields are write-only, excluded from logs, traces, errors, caches and GET responses. Store only secret-manager references in application tables.

| Verb | Route under `/workspaces/{workspace_id}` | Request / response |
|---|---|---|
| `POST` | `/inference-connections` | Required Idempotency-Key; kind, display name and either custom endpoint/protocol/model/credential or managed Kaggle credential/account binding. Returns 201 with sanitized metadata, version and configured state; does not start compute |
| `GET` | `/inference-connections` | Owner's paginated connection metadata, tested modes, readiness and sanitized error reason |
| `GET` | `/inference-connections/{id}` | Same scoped metadata plus provisioning/cleanup job IDs; never credentials |
| `PATCH` | `/inference-connections/{id}` | Required Idempotency-Key and expected config version; changes create an immutable new version, invalidate readiness and return 200. Version race returns 409 |
| `POST` | `/inference-connections/{id}/validate` | Idempotent synthetic probe job; 202 with job ID. No user documents sent during validation |
| `POST` | `/inference-connections/{id}/start` | Managed Kaggle only, explicit idempotent provisioning job; 202. Disabled feature/platform gate returns 409 |
| `POST` | `/inference-connections/{id}/stop` | Managed Kaggle only, idempotent stop job; 202. Blocks new dispatch immediately; cleanup may remain pending |
| `DELETE` | `/inference-connections/{id}` | Idempotent revoke plus cleanup job; 202. Local use blocked immediately, secrets removed per SECURITY; remote cleanup not presumed complete |

Only supported protocol adapters are accepted; validate all URLs under SECURITY before probing. Baseline remote setup requires an endpoint credential; the operator-controlled local loopback exception is in INFERENCE.md. A managed start requires platform enablement plus valid account permission; a Kaggle key alone does not establish runtime readiness. Validate refreshes capability evidence, never silently changes the approved mode/release. Connection states and immutable versions are owned by DATA_MODEL.

Lifecycle transitions reject attempts to validate/start a stopping or revoked connection; a validate request probes an existing endpoint and never starts a notebook.

Management idempotency records use a keyed digest for secret-bearing bodies, not raw keys or unkeyed secret hashes. Same key/body replays the original resource/version/job; differing bodies conflict. Recheck ownership/revocation before acting. Start/stop mutations and their durable jobs commit together. Credential replacement or endpoint edits stop new steps on old versions; running queries fail if their pinned version is no longer active.

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

For model-backed queries, require `connection_id` or resolve an explicitly configured local-only default connection. Validate ownership, ready state and mode capability; pin its current config version and capability/model/release snapshot transactionally. Do not accept model-backed work against a starting runtime. Reject ordinary endpoints requested as Self-RAG with MODEL_NOT_READY; unavailable connections return CONNECTION_UNAVAILABLE (known quota exhaustion: INFERENCE_QUOTA_EXCEEDED). Deterministic comparisons need no connection and reject a supplied connection to avoid implying model use. Connection setup is separate from request submission; request bodies never include API keys or raw endpoint overrides.

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
  "inference":{"connection_id":"<connection_uuid>","config_version":1,"model_id":"meta-llama/Llama-3.2-3B-Instruct","model_revision":"<pinned_revision>","release_manifest_hash":null,"generation_used":true}
}
```

The result inference object is null when no generation was used. Provider-managed revision aliases must be labeled as such; no invented checkpoint hash. Traces and stored runs retain the admitted identity even on failure. The example above illustrates a configured baseline runtime, not a deployed model.

Examples are illustrative; real offsets must be computed from exact stored text. Comparison results use `deterministic_comparison` and the rule in [EVIDENCE.md](EVIDENCE.md), with source-backed calculation records. `trace_id` equals `query_run_id` in v1. Internal claim/source checks and rejected candidates are retained with verifier revisions, but unauthorized content is never exposed.

## 4. Events, cancellation and source drilldown

There is one execution entry point. Streaming is a read subscription: `GET /workspaces/{workspace_id}/queries/{query_run_id}/events` with `Accept: text/event-stream`. Without that Accept header it returns paginated persisted JSON events. There is no streaming creation endpoint or `stream` request flag.

```text
id: 1
event: trace_event
data: {"query_run_id":"<uuid>","seq":1,"type":"retrieval_started","data":{"requested_by":"policy"}}

id: 2
event: answer
data: {"query_run_id":"<uuid>","seq":2,"answer":"Insufficient evidence.","citations":[],"grounding_status":"insufficient_evidence"}

```

Event IDs are decimal `seq`, monotonically increasing per run. Persist events before sending. Reconnect accepts `Last-Event-ID` or `after_seq` (both must agree if present), authorizes the run again, and replays strictly greater sequences. Invalid/future cursors return 400; purged event history returns `410 EVENTS_EXPIRED`, with a result link. Clients deduplicate by `(query_run_id, seq)`. Wire delivery is at least once; DATA_MODEL.md enforces exactly one durable terminal `answer` or `error` outcome. A terminal replay closes after the terminal event; reconnect after its sequence returns 204. A heartbeat comment is not a persisted event.

Trace types: `policy_decision`, `generation_started`, `reflection_token`, `retrieval_started`, `retrieval_completed`, `passage_found`, `passage_filtered`, `candidate_scored`, `citation_verified`, `warning`. Terminal types: `answer`, `error`. Cancellation uses terminal `error` with `QUERY_CANCELLED`. Only trained model output produces `reflection_token`; policy overrides are separate events. No hidden reasoning or unverified answer-token streaming in v1. Trace payloads contain bounded metadata/source IDs, not raw source text or system prompts.

Disconnecting a subscription does not cancel or restart work. `POST /workspaces/{workspace_id}/queries/{query_run_id}/cancel` idempotently sets cancellation intent, returns 202 for pending cancellation, or 200 with the existing terminal result. Workers check between stages and generation steps; cancellation/finalization contend on the same run row, so only one terminal transition wins. Enforce execution deadlines and lease recovery even when no browser is connected.

Retain event history for the configured period after termination; ongoing runs are never pruned. Result polling remains available until its retention/deletion policy removes it. Authorization/deletion is rechecked before each replay batch and response. If a source has been deleted, suppress historical answer/event content and return `410 SOURCE_UNAVAILABLE`; do not emit a second terminal event for an already finished run. Bytes already delivered cannot be recalled.

`GET /workspaces/{workspace_id}/citations/{citation_id}` resolves the pinned source version, page/element, raw quote and geometry after authorization. Browser navigation uses these stored values, never coordinates guessed by the model. A deleted source is unavailable, even if an old cached answer exists. Page rendering and downloads go through the authorized API in v1, avoiding stale public or signed links that bypass deletion checks.

## 5. Investigations

Work-creating POSTs require the same scoped Idempotency-Key contract as query/upload creation and persist their resource plus durable job atomically. The first rule is grand_total_match_v1 in EVIDENCE.md; more advanced rules must not be silently inferred.

| Verb | Route | Notes |
|---|---|---|
| `POST` | `/workspaces/{workspace_id}/investigations` | Create investigation with source `document_ids` |
| `POST` | `/workspaces/{workspace_id}/investigations/{id}/run` | Start deterministic extraction/link/validate task |
| `GET` | `/workspaces/{workspace_id}/investigations/{id}` | Status, rule version, linked records and findings |
| `GET` | `/workspaces/{workspace_id}/investigations/{id}/findings` | Computed discrepancies and evidence |
| `POST` | `/workspaces/{workspace_id}/investigations/{id}/reports` | Create PDF or CSV report job |
| `GET` | `/workspaces/{workspace_id}/reports/{report_id}` | Authorized report metadata and URL/stream |

Finding example:

```json
{
  "type": "AMOUNT_MISMATCH",
  "status": "verified",
  "currency": "INR",
  "purchase_order_amount": "1000.00",
  "invoice_amount": "1100.00",
  "difference": "100.00",
  "rule_id": "grand_total_match_v1",
  "match_basis": {"field": "po_number", "value": "PO-100"},
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
| 504 | `INFERENCE_TIMEOUT` | Inference deadline exceeded; late output discarded |

## 7. Versioning and compatibility

Schema names, enum values and trace types are a proposed v1 starting contract. Once executable clients consume them, avoid unversioned breaking changes. Maintain OpenAPI snapshot tests and a typed frontend client generated from the actual FastAPI schema.

## Native slice implementation notes

Local Bearer access and configured workspace scope are implemented; public auth/workspace creation are not. Upload/retry/reprocess require Idempotency-Key. Lists use opaque UUID cursors and source reads honor extraction versions. Native paragraph blocks expose raw/normalized text, page-text start/end ranges and rotated-page normalized boxes; native confidence is null. Unsupported/corrupt/encrypted/limit failures use the stable error shape. The native worker completes or marks needs_review without indexing. /ready checks schema/store/broker connectivity, not worker throughput. See ADR-022 and LOCAL_DEVELOPMENT for exact implemented scope.
