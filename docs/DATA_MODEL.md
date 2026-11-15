# Data model and provenance contract

This is the full DocuMind schema contract. Migrations 0001–0003 implement PDF extraction, 0004–0005 implement local search and queries, and 0006 implements bounded deterministic comparisons. Use PostgreSQL and Alembic for canonical records. Qdrant and BM25 are rebuildable search indexes. UUID examples are illustrative.

This file is authoritative for persistent enums, lifecycle states and provenance. [INGESTION.md](INGESTION.md) owns recovery/publication procedures; [API_CONTRACT.md](API_CONTRACT.md) owns transport; [EVIDENCE.md](EVIDENCE.md) owns support checks. Other docs must reference these rules instead of defining alternative states.

## 1. Cross-cutting conventions

- Primary keys are UUID, timestamps UTC `timestamptz`, workspace-scoped resources carry `workspace_id`.
- Source PDF and image `page_number` are **1-based**. `source_locator` supports format-specific locators such as `{ "type":"spreadsheet", "sheet":"Sheet1", "range":"B4:D7" }`; don't fake physical PDF pages for spreadsheets.
- `bbox_norm=[x0,y0,x1,y1]`: decimal coordinates normalized to `[0,1]`, origin top-left, relative to the original rendered page, with `x0 <= x1`, `y0 <= y1`.
- `content_hash` is SHA-256 of original bytes. `ingestion_version` includes parser/config revisions; stable document IDs survive reindexing.
- Preserve **raw text**, **normalized text**, extraction origin, confidence and source coordinates separately. `null` confidence means not provided by an algorithm, not 100%.
- Monetary values use `numeric(20,6)` or an equivalent Decimal field and ISO 4217 currency code. Display rounding is a separate concern.
- Each human-facing reference is anchored in a real page, element or spreadsheet cell. Chunks may span elements but must preserve a list of source anchor ranges.

## 2. Canonical tables

### Workspaces and ingestion

| Table | Key columns | Index / rule |
|---|---|---|
| `workspaces` | `id`, `name`, `active_publication_id`, `created_at` | All owned resources refer to workspace; publication pointer changes transactionally |
| `documents` | `id`, `workspace_id`, `filename`, `media_type`, `sha256`, `object_key`, `lifecycle_status`, `latest_ingestion_version`, `preview_ingestion_version`, `created_at` | Unique `(workspace_id, id)`; upload retries are deduplicated by request key, not file hash |
| `ingestion_versions` | `workspace_id`, `document_id`, `ingestion_version`, `parser_version`, `config_hash`, `extraction_status`, `index_status`, `error_code` | Unique `(workspace_id, document_id, ingestion_version)`; immutable completed content |
| `jobs` | `id`, `workspace_id`, `document_id?`, `ingestion_version?`, `kind`, `idempotency_key`, `status`, `attempts`, `lease_token`, `lease_expires_at`, `next_attempt_at`, `error_code`, `started_at`, `finished_at` | Durable dispatch record; unique `(workspace_id, kind, idempotency_key)`; ingestion/query/report/delete and connection validate/start/stop/cleanup use this shared mechanism |
| `document_pages` | `id`, `workspace_id`, `document_id`, `ingestion_version`, `page_number`, `width`, `height`, `render_object_key`, `native_text_quality`, `processing_status` | Unique `(workspace_id, document_id, ingestion_version, page_number)` |
| `document_elements` | `id`, `page_id`, `document_id`, `workspace_id`, `ingestion_version`, `element_type`, `text_raw`, `text_normalized`, `bbox_norm`, `reading_order`, `source_type`, `confidence`, `parent_element_id`, `table_coordinates`, `source_locator` | Indexed by `(document_id, page_id, reading_order)` |
| `document_chunks` | `id`, `workspace_id`, `document_id`, `ingestion_version`, `text`, `token_count`, `element_anchors`, `embedding_model_version`, `indexed_at` | Chunks unique by `(document_id, ingestion_version, id)`; anchor IDs must exist |

### Lifecycle and visibility

There is no overloaded document `status=ready`. Document metadata exposes the latest version's `extraction_status`, `index_status`, both latest/preview version IDs, and the published version separately.

| Field | Values | Transitions / meaning |
|---|---|---|
| `lifecycle_status` | `active`, `deleting`, `deleted` | Only `active -> deleting -> deleted`; deleting is immediately inaccessible |
| `extraction_status` | `queued`, `processing`, `complete`, `needs_review`, `failed` | queued -> processing -> complete/needs_review/failed; failed -> queued only by explicit retry; expired leases or retryable attempt failures within budget return processing -> queued |
| `index_status` | `not_started`, `queued`, `building`, `published`, `failed` | not_started -> queued -> building -> published/failed; failed -> queued; a newer publication supersedes membership without mutating old content |
| job `status` | `queued`, `running`, `succeeded`, `failed`, `cancelled` | Durable work state, distinct from document extraction; recovery rules in INGESTION.md |
| publication `status` | `building`, `published`, `failed`, `retired` | Published means a complete immutable snapshot; workspace pointer selects the current one |

`preview_available` is true for an active document with a completed or needs-review extraction that has source pages. `searchable` is true only for an active document with a completed extraction in the workspace's currently published snapshot. These booleans are derived, not independently mutable. Phase 1A completes extraction with `index_status=not_started`, `preview_available=true`, `searchable=false`.

Reprocessing creates a new version; it never overwrites the previous extraction. The previous published version remains searchable while the latest version is processing or failed. Reads default to `preview_ingestion_version` but citations always supply their pinned version. Review corrections create a new version with reviewer provenance; they do not mutate cited text. Indexing requires `extraction_status=complete`; needs-review data may be previewed but is excluded until a reviewed version completes.

| Additional table | Key columns | Integrity |
|---|---|---|
| `request_keys` | `workspace_id`, `principal_id`, `operation`, `key`, `request_hash`, `resource_id` | Unique scope/key; same hash replays resource, different hash conflicts; retained for resource lifetime |
| `index_publications` | `id`, `workspace_id`, `status`, `parent_publication_id`, `manifest_hash`, `qdrant_collection`, `bm25_object_key`, `retrieval_revision`, `created_at` | Unique `(workspace_id, id)`; immutable manifest after publication |
| `publication_documents` | `workspace_id`, `publication_id`, `document_id`, `ingestion_version` | One version per document per snapshot; scoped FKs |
| `publication_chunks` | `workspace_id`, `publication_id`, `chunk_id` | Exact shared dense/sparse corpus membership and hash |
| `chunk_anchors` | `workspace_id`, `chunk_id`, `ordinal`, `document_id`, `ingestion_version`, `element_id`, `start_char`, `end_char` | Canonical relational anchors; `element_anchors` is a serialized projection, not independent truth |

`document_elements.page_id` and `citations.page_number` may be null for truly nonpaginated source formats only when a valid `source_locator` is stored. For PDF/images they must resolve to a physical page.

`document_elements.element_type`: `title`, `heading`, `paragraph`, `list_item`, `table`, `table_cell`, `caption`, `figure`, `footer`, `header`, `other`. Avoid claiming a parser can recognize all types with equal accuracy.

`source_type`: `native_text`, `ocr`, `structured_file`, `manual_correction`. Store `extractor_name`, `extractor_version` in a metadata JSON column.

### Inference connections and execution identity

Add these tables with Phase 2, not the native ingestion migration. INFERENCE.md owns behavior; persistent values are defined here.

| Table | Key columns | Integrity |
|---|---|---|
| `inference_connections` | `id`, `workspace_id`, `owner_principal_id`, `kind`, `display_name`, `active_config_version`, `status`, `unavailable_reason`, `external_resource_id?`, `cleanup_job_id?`, `created_at`, `revoked_at?` | Unique scoped ID; owner and workspace authorization required; external resource IDs restricted to owner/control-plane reads |
| `inference_connection_versions` | `workspace_id`, `connection_id`, `config_version`, `endpoint_url?`, `provider_protocol`, `model_id`, `account_credential_ref?`, `endpoint_credential_ref?`, `release_manifest_hash?`, `created_at` | Immutable config; unique `(workspace_id, connection_id, config_version)` with scoped parent FK; no plaintext secrets |
| `inference_capability_checks` | `id`, `workspace_id`, `connection_id`, `config_version`, `supported_modes`, `model_identity`, `runtime_instance_id?`, `protocol_version`, `release_manifest_hash?`, `probe_artifact_ref`, `trust_basis`, `checked_at` | Immutable evidence for that exact version; synthetic probe artifacts only; connection status selects current readiness |
| `generation_steps` | `id`, `workspace_id`, `query_run_id`, `ordinal`, `state`, `request_hash`, `status`, `input_tokens?`, `output_tokens?`, `deadline`, `response_artifact_ref?`, `error_code?` | Unique `(workspace_id, query_run_id, ordinal)`; persist intent before dispatch and completion/usage before next step. Private source-derived artifact, never a public trace payload |

The current local app persists generation connections as `custom_endpoint`. Local servers, private Kaggle loopback bridges and remote APIs share this versioned generation contract. Its implemented states are `configured`, `ready`, `unavailable` and `revoked`. Validation binds an immutable configuration version. Edits invalidate readiness and erase old credentials. Revocation is terminal. Kaggle allocation state belongs to the private operator state file and lifecycle command. The earlier `managed_kaggle` connection design and hosted principal/account tables above are historical targets superseded by ADR-042.

Generation-step `status`: `pending`, `in_flight`, `completed`, `failed`, `cancelled`, `unknown`. Persist pending -> in_flight before outbound dispatch, then one terminal completion/failure/cancel. Crash or ambiguous timeout can yield unknown; only recovery of the original response and usage may promote unknown to completed under a still-active run, otherwise fail the query. Never regenerate unknown work automatically. Confirmed retry safety and shared budgets follow INFERENCE.md.

Account/control-plane and endpoint/inference secrets use distinct references and are never forwarded interchangeably. Secret rotation uses a new credential reference/config version. Revocation prevents all old-version use. Historical connection metadata may be retained for run provenance after secret erasure; deleting the secret must not cascade-delete query history. Store secret-bearing request idempotency only as keyed digests. Ordinary result queries expose no endpoint credentials, tunnel tokens or provider account identifiers.

### Queries and evidence

| Table | Key columns | Index / rule |
|---|---|---|
| `query_runs` | `id`, `workspace_id`, `principal_id`, `question`, `query_type`, `mode`, `status`, `publication_id`, `effective_document_ids`, `connection_id?`, `connection_config_version?`, `capability_check_id?`, `runtime_instance_id?`, `model_id?`, `model_revision?`, `release_manifest_hash?`, `generation_used`, `retrieval_config_revision`, `cancel_requested_at`, `started_at`, `completed_at` | Pin scope, connection/capability and model/release revisions once; status queued/running/succeeded/failed/cancelled |
| `query_events` | `id`, `query_run_id`, `seq`, `event_type`, `payload_json`, `occurred_at` | Unique `(query_run_id, seq)`; no secret chain of thought |
| `answers` | `id`, `query_run_id`, `text`, `grounding_status`, `latency_ms`, `retrieval_calls` | One terminal answer per completed query run |
| `citations` | `id`, `workspace_id`, `answer_id`, `claim_id`, `claim_span_start`, `claim_span_end`, `document_id`, `ingestion_version`, `page_number`, `element_id`, `chunk_id?`, `start_char`, `end_char`, `source_quote`, `verification_status` | Scoped FK provenance; chunk optional only for structured evidence backed by an element |
| `claims` | `id`, `workspace_id`, `answer_id`, `claim_span_start`, `claim_span_end`, `support_status`, `verification_method`, `verifier_version`, `calculation_id?` | support_status supported/unsupported/unknown; checks specified in EVIDENCE.md |
| `retrieval_observations` | `id`, `query_run_id`, `chunk_id`, `stage`, `dense_rank`, `sparse_rank`, `rrf_score`, `rerank_score`, `decision`, `reason` | Scores nullable when stage unavailable; no fabricated cross-score ranking |

`mode`: `baseline_rag`, `self_rag`, `deterministic_comparison`. `query_type`: `GENERAL`, `DOCUMENT_QA`, `CROSS_DOCUMENT`, `CALCULATION`, `UNSUPPORTED`.

`grounding_status`: `supported`, `partially_supported`, `insufficient_evidence`, `unverified` (can expand with documented migration). Each citation's verification status must be based on actual evidence checking; `source_quote` must equal the raw source slice identified by its offsets; normalized matching resolves back to raw offsets before persistence.

`verification_status`: `supported`, `unsupported`, `unknown`; store identity/span check results separately. Citation quotes are raw source excerpts resolved through offsets. Answers have a unique `query_run_id`. Events have unique `(query_run_id, seq)` and a partial unique index on `query_run_id WHERE event_type IN ('answer', 'error')`. Final run status, answer (on success), and terminal event commit in the same PostgreSQL transaction. Cancellation produces terminal `error` with `QUERY_CANCELLED`. Event delivery can repeat; durable terminal outcomes cannot.

### Structured records and investigations

The table below describes the broader target. See [Phase 4 local persistence](#phase-4-local-persistence) for the implemented fields.

| Table | Key columns | Rule |
|---|---|---|
| `business_entities` | `id`, `workspace_id`, `canonical_name`, `external_identifier`, `entity_type` | No automatic high-confidence merge by name alone |
| `extracted_records` | `id`, `workspace_id`, `document_id`, `ingestion_version`, `record_type`, `record_key`, `fields_json`, `extraction_version`, `review_status` | Validated field schema, decimal strings and canonical relational evidence links |
| `record_links` | `id`, `workspace_id`, `left_record_id`, `right_record_id`, `link_type`, `score`, `method`, `status` | Ambiguous matches remain proposed or under review |
| `investigations` | `id`, `workspace_id`, `name`, `document_ids`, `status`, `rule_set_version`, `created_at` | Explicit set of comparison documents |
| `findings` | `id`, `investigation_id`, `type`, `severity`, `status`, `calculation_json`, `explanation`, `evidence_anchors`, `created_at` | Deterministic values stored separately from explanation |
| `report_artifacts` | `id`, `investigation_id`, `format`, `object_key`, `generation_version`, `created_at` | Generated only from persisted findings |

A record field should support:

```json
{
  "name": "grand_total",
  "raw_value": "1,100.00",
  "normalized_value": "1100.00",
  "data_type": "money",
  "currency": "INR",
  "confidence": 0.93,
  "evidence": [{"document_id": "<uuid>", "page_number": 2, "element_id": "<uuid>", "source_quote": "Total INR 1,100.00"}],
  "normalizer_version": "money-v1"
}
```

The example is a *schema illustration*, not a claimed extraction.

## 3. Passage object: service interface

```python
@dataclass(frozen=True)
class SourceAnchor:
    document_id: UUID
    ingestion_version: str
    page_number: int | None
    element_id: UUID | None
    start_char: int | None
    end_char: int | None
    source_locator: dict | None = None

@dataclass(frozen=True)
class Passage:
    chunk_id: UUID
    document_id: UUID
    workspace_id: UUID
    publication_id: UUID
    text: str
    anchors: tuple[SourceAnchor, ...]
    dense_rank: int | None = None
    sparse_rank: int | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
```

**Do not** anchor citations merely by `chunk_id`; chunking is a search convenience, not the source document coordinate system.

## 4. Qdrant index contract

Collection `documind_chunks_v3`: vector dimension **768** for text-only `google/embeddinggemma-2`, L2-normalized with cosine distance (validate pinned revision). The publication manifest records the complete hybrid-v3 encoding identity defined in RETRIEVAL.md; queries must use that identity. Point identity is a deterministic UUID of `(workspace_id, publication_id, chunk_id)`, so building a new snapshot cannot overwrite points used by running queries. Payload includes those IDs, `document_id`, `ingestion_version`, `embedding_version`, `page_numbers`. Require indexed workspace, publication and effective-document filters. Publication membership is canonical in PostgreSQL. An embedding schema change builds a new collection; never mix dimensions or embedding identities, even if dimensions match.

## 5. BM25 index contract

A `rank_bm25` snapshot is immutable and keyed by `(workspace_id, publication_id)`, with exact chunk IDs/hashes matching dense search. Persist corpus metadata, tokenizer revision, artifact checksum and rebuild recipe; do not load untrusted pickle objects. Cache by publication ID, not just workspace. Apply authorized document filtering before top-k selection. The initial small-workspace implementation rebuilds the full snapshot on publication; incremental indexing requires a separate measured design change.

## 6. Data deletion and revisions

- Delete first sets `lifecycle_status=deleting` and creates durable cleanup work in one transaction. All new reads/queries/downloads/streams reject it immediately; physical cleanup may retry. Cleanup removes originals, derived assets, records, all publication points and sparse artifacts containing the document, then rebuilds a publication from surviving documents. Any pinned run referencing a deleted document fails closed with `SOURCE_UNAVAILABLE`; do not silently substitute a newer revision.
- Cleanup follows FK order: mark affected runs/reports source-unavailable and retain only non-content tombstone metadata; purge their source-derived generation-step artifacts/request context, answers, quotes, claim/evidence rows, event payloads, report files and content-bearing query text before removing source elements/chunks/pages. Keep document/run IDs and terminal status for authorized unavailable responses and idempotency, never dangling live citations. Remove or invalidate derived evaluation artifacts containing the deleted content. Source immutability forbids edits, not this authorized purge. Interrupted cleanup resumes safely from its durable job.
- Every query run records corpus/index revision. If source content is deleted later, UI must not pretend citation still resolves; redact or mark unavailable according to policy.
- Migration must preserve stable IDs or provide reindex mappings.

## 7. Required migration constraints

- Every owned table carries `workspace_id`, including child events, answers, observations, findings and reports, even where omitted from the overview columns. Use scoped composite FKs, not independent workspace and resource FKs. Public authorization is still required; database consistency is not membership authorization.
- Model-backed query runs reference the same workspace/owner connection and exact config/capability version through scoped FKs and transactional owner checks; generation_steps reference their scoped query run. Connection revocation/current-version authorization is rechecked before each outbound call. Deterministic runs have null inference references.
- Pages reference `(workspace_id, document_id, ingestion_version)`; elements reference `(workspace_id, document_id, ingestion_version, page_id)`. Define corresponding unique keys on parents. Check page_number >= 1, positive dimensions, ordered normalized boxes, and nonnegative reading order.
- Chunks and `chunk_anchors` must share workspace/document/version through composite keys. Anchors reference immutable elements with the same scope. Source ranges use Unicode code points and `0 <= start_char < end_char <= length(text_raw)`; use a validating transaction/trigger for cross-row range checks, not an impossible cross-table CHECK expression.
- Element IDs are deterministic within a version (version + page + extractor output identity), and different versions receive different IDs. Promoted complete/needs-review raw/normalized text, geometry and anchors are immutable; restrict updates with database permissions/triggers. Retry staging writes use deterministic keys and are promoted only by the current lease holder.
- Citations reference scoped answer/claim/element tuples and, where present, chunk-anchor membership. The citation's page_number must agree with its element's page. Preserve old extraction rows for historical citations until explicit deletion; never resolve a citation through the latest version pointer.
- Use relational `investigation_documents`, `record_evidence`, `finding_evidence` and `query_evidence` membership rows with scoped FKs; JSON arrays in responses are projections. JSON field values must pass typed schemas, finite Decimal validation and anchor resolution on write and read. SQL operations use typed numeric columns or validated typed records, not binary floats parsed from arbitrary JSON.
- Insert a query's evidence ledger before generation; only its pinned supplied evidence or validated calculation evidence can support citations. Cross-workspace, cross-version and wrong-page insertions must fail integration tests, even when every individual UUID exists.

## 8. Critical integrity assertions

1. Every `document_element.document_id` matches its page's document.
2. Every chunk anchor resolves to a source element in that workspace/version.
3. Every citation resolves to one of the passages actually available to the answer or a verified typed record backed by a source anchor.
4. Every retrieval query uses workspace filters in **both dense and sparse** paths.
5. Every discrepancy finding stores the exact inputs, operation, rule version and source anchors needed to recompute it.
6. Missing confidence, missing currency and missing evidence must be represented as missing, not guessed.

## Phase 1A schema implementation

Migration 0001_native creates workspaces, documents, ingestion_versions, jobs, request_keys, document_pages and document_elements only. Parser config/output hashes and source offsets are stored; completed/needs-review source mutations are rejected by PostgreSQL triggers. latest/preview version pointers have deferred scoped foreign keys. No publication pointer or chunk/query tables exist before Phase 2. Job kinds are bounded to ingest/delete and index_status to not_started for this migration; widen these through later migrations. Local principal identity is the configured workspace UUID until public identity is implemented. Pages store rendered width/height/rotation and native text; elements store block ranges into page text. See ADR-022.

## Phase 1B schema implementation

Migrations 0002/0003 add `metadata_json` to pages/elements, `parent_element_id` and `table_coordinates` to elements, scoped same-page parent FKs and table-parent/span validation. Implemented element types are paragraph/table/table_cell; source types are native_text/ocr/manual_correction. The broader enum above remains a future contract. JSON coordinates must be an object with numeric nonnegative row/column indices, positive spans and boolean/null is_header. A parent must be a table in the same workspace/document/version/page, and cell source ranges must lie inside it. Only empty table cells may have start_char=end_char; all ranges must still match stored raw page text.

Table parent metadata stores row_count, column_count, headers (inferred or null) and continuation=unknown. Cells store row_index, column_index (zero-based), row_span, column_span and is_header; a null header flag is unknown. Element metadata identifies extractor/version; OCR pages include pinned model repository revisions/file hashes and rendering DPI. Normalized boxes remain relative to rotated previews, independent of logical row/column order.

Reviewed versions use the existing ingestion_versions/jobs tables with a frozen review config, reviewer ID, base version and corrections. IDs are regenerated deterministically within the new version; per-element metadata retains reviewed_from_element_id/base_ingestion_version. Review never mutates original rows or makes a document searchable. Downgrade from Phase 1B refuses to discard populated OCR/table/manual-correction provenance; a full migration roundtrip is tested only on an empty disposable DB.

## Phase 2 local persistence

Migration `0004_phase2` adds scoped publication membership, chunk anchors, endpoint versions, encrypted secret references, capability checks, query scopes, citations and events. Existing PostgreSQL leases cover publication, validation and query jobs. Query status comes from its job, while result and terminal event commit together. The single outbound step is represented by a durable dispatch marker on the run and the admitted capability and result metadata. The citation trigger enforces matching query membership and an exact raw quote inside its chunk range.

A document's published version and searchability derive from active publication membership. Extraction history stays immutable across publication changes. Deletion revokes snapshots containing the document and suppresses their pinned query results before cleanup. Cleanup removes their dense, lexical and chunk artifacts, deletes source-derived result and trace content, and clears review correction payloads while preserving tombstone IDs and hashes. The worker queues a replacement when the active pointer has been cleared. Phase 2 data prevents downgrade to the Phase 1B schema. Restore a pre-upgrade backup when a rollback is required.

Migration `0005_publication_recovery` adds `documents.last_published_ingestion_version` with a scoped extraction foreign key and a backfill from successful publications. Successful publication updates that pointer. It preserves the last complete published source for a surviving document when another source's deletion revokes the shared snapshot. The deleted document's pointer is cleared during revocation. Current searchability still requires membership in an active publication.

## Phase 4 local persistence

Migration `0006_comparisons` adds the `comparison` and `report` job kinds and these relations:

| Table | Implemented data |
|---|---|
| `investigations`, `investigation_documents` | Workspace, rule, comparison job, revocation timestamp and pinned document/version pairs |
| `extracted_records` | One typed record per selected version. Role, identifiers, ISO currency, basis, `numeric(20,6)` total and extraction issue |
| `record_evidence` | One anchored value per recognized field. Scoped page/element foreign keys and a trigger enforcing the exact Unicode source slice |
| `findings`, `finding_evidence` | One outcome, reason or `numeric(21,6)` difference. Verified outcomes associate invoice and PO grand-total anchors |
| `report_artifacts` | One CSV artifact and job per investigation, with stored bytes and SHA-256 |

Records, anchors, finding and successful job status commit together. The CSV has at most two source rows for a verified finding and one row for a needs-review outcome. Downloads recompute and compare the bytes and checksum. Revocation removes extracted fields, evidence and report bytes while retaining safe job and investigation metadata. The migration refuses to downgrade when investigations exist.

## Phase 6 local persistence

Migration `0007_reflection` adds `query_runs.mode` and `runtime_snapshot`. Baseline runs retain their required publication and optional ordinary connection version. A Self-RAG run pins the operator-approved origin, manifest, runtime instance, tokenizer token identities and context limit. General runs have no publication or document rows. Document runs retain the existing scoped publication and source-version foreign keys. Service credentials stay in server configuration.

Migration `0009_split_reflection` permits a Self-RAG run to pin a generator connection/version/capability check in addition to its reflection runtime snapshot. Existing runs retain their fields and behavior. The transient recovery journal holds provider intent, the exact generated candidate and its completed critic result. Uncertain work fails closed. Downgrade refuses when split query rows exist.

Migration `0010_artifacts_dispatch` adds nullable accepted original sizes, preview sizes and digests, and a queued-send reservation timestamp. Older rows retain their existing artifact identity. The application checks their original digest and legacy PNG structure when reading them. The reservation limits repeated broker sends independently of the worker lease.

The query trace records step IDs, request hashes, budgets, usage and validated labels. Migration `0008_reflection_recovery` adds a private bounded journal for validated responses and the ordered IDs of the retrieved chunks. It contains temporary candidate text and is cleared on completion, failure, cancellation or source revocation. Public traces never contain candidate text. A replacement worker reconstructs the same query history under its new lease. An unconfirmed external operation fails the run. Downgrade refuses to discard active recovery state or stored Self-RAG runs.
