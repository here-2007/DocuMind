# Ingestion and document understanding

The original PDF uses PyMuPDF for extracting a text QA corpus (p. 5). All OCR, document layout, tables, spreadsheet support, background jobs and provenance requirements here are **DocuMind extensions**.

## 1. Goals

Recover usable content **without losing source identity**. The output is a canonical representation of pages, text, layout and table cells, with enough metadata to jump from an answer or discrepancy back to the original. OCR errors must never silently become verified facts.

## 2. Format roadmap

| Format | Primary adapter | Status target |
|---|---|---|
| Native text PDF | PyMuPDF, Docling for layout if beneficial | MVP 1 |
| Scanned/image-only PDF | Render pages + PaddleOCR fallback | MVP 2 |
| PNG/JPG/TIFF (if supported by adapter) | Image pipeline + OCR | MVP 2 |
| DOCX | Structure aware parser (Docling or compatible) | Extended |
| CSV | Typed tabular adapter with encoding/dialect checks | Extended |
| XLSX | Sheet/cell adapter, formula vs cached value handling | Extended |

Do not treat an OCR library or Docling as guaranteed to parse every version/format; verify installed capabilities and create parser fallback/error states.

## 3. Processing steps

1. **Validate:** reject unsupported files and enforce file size, page and workspace document limits. Record the original SHA-256 and object key for accepted uploads.
2. **Render/inspect:** for PDFs, record page dimensions and page images. For scanned PDF pages, OCR applies to rasterization at an empirically chosen resolution; log resolution and processing time.
3. **Extract native content:** read text spans/coordinates via PyMuPDF; preserve reading order candidates, raw text and font/section metadata where available.
4. **Choose OCR:** use configurable low-text threshold per page or image-only detection; avoid blindly OCRing high-quality native PDFs. OCR supplies text, normalized geometry, language where supported and meaningful confidence.
5. **Layout parse:** identify sections/paragraphs/tables. Validate reading order, table continuity across pages, headers and footers.
6. **Table capture:** represent table + each cell with row/column coordinates, headers, source geometry, page, extraction confidence. Preserve currency/symbol characters and multiline content.
7. **Normalize carefully:** whitespace cleanup, Unicode normalization and typed fields must not overwrite `text_raw`; preserve identifiers like `INV-001/24` and local numeric separators.
8. **Complete extraction:** validate immutable page/element anchors, mark the version's `extraction_status=complete`, and set the preview pointer. Phase 1A ends here with `index_status=not_started`. A partial or uncertain extraction is `needs_review` and previewable with warnings, never silently searchable.
9. **Chunk/index (Phase 2):** use `hybrid-v3` budgets from [RETRIEVAL.md](RETRIEVAL.md), then build and publish a matching dense/sparse snapshot. Preview availability does not depend on search availability; lifecycle fields are defined only in [DATA_MODEL.md](DATA_MODEL.md).

## 4. Page and element mapping

- PDF coordinates often use page-space points; convert source box to normalized top-left format with explicit rotation handling. Test 0/90/180/270-degree rotations.
- `page_number` always 1-based on API/DB. For DOCX/spreadsheets, provide `source_locator` (paragraph/table/sheet-cell) where physical page isn't stable; do not invent a page number.
- Every chunk stores an array of source ranges; citation verification traverses this anchor list.
- For multi-page tables, preserve `table_id`, split markers, inferred header repeat status and source page for each cell.

## 5. Field extraction for business documents

Initial record types and fields:

| Type | Typical fields | Notes |
|---|---|---|
| `invoice` | invoice number, vendor, buyer, issue/due dates, PO reference, line items, subtotal, tax, discount, grand total, currency | May have multiple taxes/credits |
| `purchase_order` | PO number, buyer, vendor, line items, unit price, quantity, tax, currency, total | May specify price basis and tolerance |
| `contract` | parties, effective/expiry dates, referenced PO, payment terms, rates, currency, amendment/reference | Clause ambiguity requires caution |

For every field, store raw value, normalized typed value, source anchor, extraction method, confidence and human review status. Use deterministic parsing for dates/currencies where safe and LLM extraction only when properly grounded/validated. Duplicate invoices, multiple amended POs and international tax cases are explicit edge cases.

## 6. Durable dispatch and recovery

Each staged page write takes the same document advisory lock as physical deletion, renews/checks its job lease inside that lock and releases it after the atomic write. Cleanup holds the lock through the object sweep and deletion receipt. The final promotion transaction still checks visibility and lease ownership. A worker paused between heartbeat and write therefore cannot recreate an object after successful cleanup. Document and publication artifact locks use nonblocking acquisition; contention returns retryable `PUBLICATION_BUSY` instead of occupying a worker beyond its lease. Exhausted retries remain visible and retain the quota slot.

The `jobs` table is the durable work queue/outbox; Celery is an at-least-once delivery mechanism. This is implemented inside the existing API/workers, not an additional service.

1. Upload validates and writes original bytes to a private immutable object key, then commits the request key, document, ingestion version and queued job in one PostgreSQL transaction. Return 202 only after commit. An object written before a failed transaction is an orphan; a periodic reconciler removes unreferenced objects after a grace period. Never acknowledge durable upload before the original exists.
2. Require an idempotency key on upload. Hash the canonical request metadata plus server-computed content hash. Concurrent same-key requests resolve to one persisted document/job; differing content returns `409 IDEMPOTENCY_CONFLICT`. Deliberate reuploads with new keys may create new documents; do not impose hash-based deduplication silently.
3. A dispatcher scans due queued jobs and reserves each send atomically in PostgreSQL before publishing the job ID. A queued job with a recent reservation can be sent again after 60 seconds, including after an uncertain broker response or dispatcher crash. A worker can claim immediately after delivery and clears the send reservation on claim. The worker lease still fences duplicate deliveries and counts processing attempts independently of sends.
4. Workers heartbeat their lease and check cancellation/deletion at stage boundaries. Completion and stage promotion require the current lease token. A stale worker cannot overwrite a newer attempt. Expired leases return to queued; transient failures use capped backoff, and exhausted attempts become failed. Defaults are in `.env.example`; terminal failure requires an explicit retry for retryable non-query work. Terminal query runs are immutable and require a new query/key, not reopening their job.
5. Idempotency for ingestion is `(document_id, content_hash, parser_version, config_hash)`; its hash determines `ingestion_version`. Stage object keys and row IDs also include that version. Staging outputs are never served; final page/element promotion and extraction completion commit together. If retry output disagrees with an already promoted version checksum, reject it as a reproducibility error instead of overwriting content. An explicit reprocess with a changed parser/config creates a new version and preserves old citations.
6. Query/report/deletion jobs use the same durable dispatch and lease protocol, with their own unique resource/work keys. A crashed query may recompute deterministic stages, but unknown remote generation steps follow INFERENCE.md: recover the original response/usage or fail, without automatic regeneration or budget reset. Only the current lease holder can persist the single terminal outcome. Never claim exactly-once model execution. Connection provisioning/validation/cleanup uses this same leased job mechanism; reconcile external resource IDs before retrying creation to avoid duplicate GPU sessions.

## 7. Publishing a consistent search snapshot

1. Serialize publication builders per workspace using a database lease; no model inference occurs inside an open DB transaction. Freeze a manifest of eligible document/extraction versions, retrieval revision and exact chunk IDs/hashes. Record the parent publication ID.
2. Build a full replacement snapshot for the initially small workspace: Qdrant points keyed by publication plus chunk, and a private BM25 artifact with the same corpus. Retain the old active publication while building. Await durable vector writes and verify both membership manifests/checksums; never publish one branch alone.
3. In a short transaction, verify the builder lease, source visibility and unchanged parent pointer, mark the snapshot published, update the workspace pointer and the included versions' index states. If a concurrent publication or deletion invalidated the build, abandon/rebuild; never overwrite the newer pointer.
4. A query pins the publication ID and effective document/version set once. Every retrieval in that run uses that snapshot; caches include workspace, publication, selected-document set, query and model/config revision. Both indexes missing or mismatched is an explicit dependency error. No fallback to another snapshot mid-query.
5. Validate results against canonical PostgreSQL membership and current access before sending them to a model or client. Deletion wins over pinned revisions: a run referencing a newly deleted source fails with `SOURCE_UNAVAILABLE`. Stored answers and reports are access-checked again on every read.
6. Garbage-collect failed staging snapshots after a grace period. The local implementation keeps retired snapshots for replay and evaluation until source deletion. Add retention-based cleanup only after checking active pins and the replay/evaluation retention policy. Immutable source elements remain for citations until explicit deletion. Evaluation manifests record artifact hashes. Deletion may make a historical run non-reproducible.

Crash tests must kill workers after original storage, DB commit, queue submission, page staging, dense writes, sparse writes and publication commit. Restart must converge to either a complete visible version or an explicit recoverable failure, never mixed-version search.

## 8. Quality metrics

- OCR: character error rate/word error rate on labeled test images, confidence calibration where feasible.
- Layout: element class precision/recall or task-specific mAP where bounding boxes are labeled.
- Table extraction: cell text exactness, row/column structure accuracy and numeric value correctness.
- Key fields: precision, recall, F1, exact normalized value match and document linkage accuracy.
- Citation anchor quality: correct source page, correct text span and box overlap on reviewed samples.

Keep metrics separated by document type and scan quality; pooled averages can hide catastrophic failures on poor scans.

## Implemented PDF extraction (Phase 0/1A/1B)

The native slice implements leased DB dispatch, parser/config identity, immutable atomic source promotion, preview reads, retries, reprocessing and cleanup. Phase 1B adds optional English CPU PaddleOCR, bounded ruled tables and human review (ADR-024). Parsing remains isolated in a time-bounded subprocess; worker memory/process limits and exact commands are in LOCAL_DEVELOPMENT.md. Search publication above is Phase 2.

Under the workspace write lock, upload admission checks for an accepted replay before counting documents that still occupy storage. A new upload at capacity creates no original, database rows or job. Deletion revokes access immediately and releases a slot after cleanup marks the document deleted.

Accepted originals retain their byte-size receipt and SHA-256 independently of later upload settings. New previews retain a size and digest per page. An older original uses its existing recorded digest, and an older PNG uses the stored page geometry as a read bound and checks its PNG structure and checksums. Cleanup retries temporary dense index failures within the existing job budget. Exhausted cleanup stays visible by job ID so an operator can retry it after restoring the dependency.

- Native text and tables: PyMuPDF extracts paragraphs and `lines_strict` ruled tables. Table-adjacent text is split at words so it is not dropped with an overlapping block. Native cells retain actual row/column spans and inferred headers. Native confidence remains null.
- Scan selection: OCR is eligible below OCR_MIN_NATIVE_CHARS (default 8 non-whitespace characters), or when embedded image area exceeds half the page. OCR replaces the page's text representation, avoiding duplicate native/OCR text; mixed PDFs select per page. Rendering occurs unrotated at OCR_DPI (default 144), with boxes transformed back to the rotated preview. Pixel budgets are checked before raster allocation.
- OCR: the fixed mobile English profile loads verified local artifacts only; setup downloads separately. Text, recognized confidence, model/package identity, DPI and normalized geometry are retained. Missing/mismatched models yield OCR_MODEL_UNAVAILABLE/OCR_MODEL_IDENTITY_MISMATCH; runtime/recognition failures are explicit. No OCR result becomes complete automatically.
- Tables: native ruled tables or complete rectangular scanned grids produce a table parent and table_cell children with stable versioned IDs, normalized boxes, row/column/span coordinates and exact ranges into canonical page text. Empty cells have equal start/end offsets. Raster grids use actual OCR for text; header flags and cross-page continuation remain unknown. Scanned merged/unruled/ragged tables and automatic continuation are not supported; OCR text is still previewable with review warnings. Table output always needs review. Multi-page tables are separate page-local tables until a later linking adapter is validated.
- Review: POST /documents/{id}/review pins a completed/needs-review source version, requires explicit whole-document confirmation and accepts optional text/header corrections for existing non-table elements. It creates durable work with parser identity manual-review-v2. The worker copies original previews, preserves reading order and rebuilds raw text/offsets and table-parent text, records reviewer/base-element provenance, then atomically promotes a new complete version. An empty page cannot be approved as successful extraction. Review does not add missing cells or repair geometry; those need a later editor/adapter. Previous source rows and images remain available by version until deletion.

Warnings are exposed in page metadata (`OCR_REQUIRED`, `NO_TEXT`, `OCR_REVIEW_REQUIRED`, `TABLE_REVIEW_REQUIRED`). A review preserves original warnings/provenance while clearing current warnings. Recognition confidence is not calibrated correctness. Review completion is a human attestation, not an automatic numerical validation; arithmetic/record rules remain later phases.

The Phase 1B regression fixes use the pdf-v3 parser revision and manual-review-v2. Table detection retains separate native text runs on both sides of a grid. Scanned grids can contain one row or one column when they have at least two cells. Each recognized line is assigned once. Selecting an already completed review advances latest and preview together. Upload replays consult the durable ledger before OCR prerequisites.

Phase 2 starts a full publication through the API. Review and extraction can finish independently of search. If a source is deleted, the workspace pointer is revoked when affected and both search artifacts are erased by cleanup before a replacement is built.
