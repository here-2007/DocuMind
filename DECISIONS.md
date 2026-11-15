# Architecture decision record (ADR) register

This is the canonical architecture and repository-workflow decision register. Each entry distinguishes planned decisions from implemented behavior; current execution status and validation belong in [PROGRESS.md](PROGRESS.md). If a decision changes, append a new ADR rather than rewriting history. Significant deviations from the source PDF must also be recorded in [docs/CHANGES_FROM_PDF.md](docs/CHANGES_FROM_PDF.md).

Current authority: ADR-049 defines Phase 8 artifact admission, scope and raw scoring, parser confinement and checkpoint admission. ADR-048 closes the first seven November 16 audit findings across storage access, artifact locks, runtime lifetime and browser recovery. ADR-047 defines the frozen Phase 8 cohorts and paired research controls. ADR-046 defines accepted artifact bounds, send reservations and deletion/comparison recovery. ADR-045 defines the workspace upload quota. ADR-044 distinguishes explicitly delegated agent review from human evidence. ADR-043 adds bound post-selection scoring without modifying a checkpoint. ADR-042 defines local-only application execution and independently selected reflection and generation models. It supersedes earlier hosted application, public account-management and single-provider restrictions in ADR-007/020/021/030/033. ADR-041 defines paired invoice evidence and checked addition. ADR-038–040 define bounded operator cleanup, query retry and executable model evidence. ADR-035–037 define exact generation budgets, generated-output review and creation-bound Kaggle cleanup. ADR-034 defines the improved training loss and independent diagnostic corpus. ADR-032 defines confirmed-step recovery. ADR-031 pins the open Qwen base. ADR-025 and ADR-024 define retrieval and extraction boundaries. ADR-023 owns context maintenance. Earlier entries remain as history.

## ADR-001: Product domain replaces GATE/UPSC

**Status:** accepted as DocuMind product requirement.

**Decision:** focus on document QA and linked invoice/PO/contract investigation. Keep original Self-RAG research architecture.

**Consequence:** new parser, page provenance, table extraction, structured data, evaluation labels and more engineering work; cannot reuse original PDF's GATE/UPSC benchmarks.

## ADR-002: Next.js/FastAPI instead of Streamlit

**Status:** accepted as target design, **not** yet implemented.

**Decision:** browser-facing Next.js/TypeScript and Python FastAPI with SSE traces.

**Alternatives:** Streamlit (faster proof of concept, matches PDF); a single monolithic Python UI.

**Consequence:** richer file viewer/workflow but significantly larger frontend/backend integration and deployment surface. A temporary internal Streamlit evaluation dashboard is not prohibited, but not the product UI.

## ADR-003: Baseline first, fine-tuning later

**Status:** accepted.

**Decision:** build native PDF ingestion, provenance, retrieval, cited naive RAG and minimal UI before QLoRA training.

**Alternatives:** source PDF's training-first six-week plan.

**Consequence:** longer 16-week proposed roadmap; dramatically easier diagnosis of retrieval/annotation failures and more useful partial product.

## ADR-004: Preserve hybrid retriever exactly as research baseline

**Status:** accepted as experimental starting point.

**Decision:** Qdrant with all-MiniLM-L6-v2, BM25 `rank_bm25`, 512/64 chunking, RRF 60, top-20 cross encoder rerank to top-5.

**Consequence:** easiest architecture-level comparison with PDF. Changes for tables/IDs must be versioned and separately ablated, not silently mixed into baseline metrics.

## ADR-005: Typed records + deterministic validation

**Status:** accepted as DocuMind addition.

**Decision:** Postgres extracted records, explicit record linking, `Decimal` arithmetic with rule versions, original page citations.

**Alternatives:** ask LLM to compare all amounts directly.

**Consequence:** more implementation and labeling, but credible auditability and numerical correctness.

## ADR-006: Evidence policy overrides retrieval skipping

**Status:** accepted.

**Decision:** private uploaded-document facts, cross-document findings and calculations require scoped retrieval. Model can skip only on eligible non-document questions.

**Consequence:** unconditional skip-rate gains on document datasets may be low; must report eligible vs required subsets. Better factual safety and trace clarity.

## ADR-007: Modular monolith + workers before distributed microservices

**Status:** proposed target architecture.

**Decision:** FastAPI service with internal typed modules, Celery workers, Postgres, Qdrant, object storage and Redis; separate GPU process only when needed.

**Consequence:** manageable local setup and one API boundary. Public deployment still requires security, scaling and GPU hosting choices.

## ADR-008: Source-aware citations from first ingestion

**Status:** accepted.

**Decision:** each answer cites original document/page/element (or format-appropriate locator) and quote, not only a retrieved chunk number.

**Consequence:** page geometry, extraction versioning and source viewer cannot be deferred indefinitely.

## ADR-009: Reflection-token grammar is a versioned interface

**Status:** proposed; **requires paper alignment review before training**.

**Decision:** document a complete control-symbol vocabulary and categorical labels, register tokenizer IDs, save embedding configuration and test resume behavior.

**Consequence:** PDF shorthand names alone are insufficient. Exact implementation may be described as Self-RAG-inspired if not paper faithful.

## ADR-010: Synthetic linked-document benchmark when real pairs unavailable

**Status:** proposed, licensing and realism review pending.

**Decision:** generate controlled invoice/PO/contract bundles with explicit gold findings while also using licensed public document benchmarks for extraction and QA.

**Consequence:** measurable arithmetic/citation correctness, but synthetic performance cannot be generalized to real-world documents without external validation.

## Open decisions (must be resolved before production release)

| ID | Question | Trigger / decision criteria |
|---|---|---|
| O01 | Which object store and authentication provider? | Before any public multi-user deployment; compare costs/security |
| O02 | Which language(s), OCR models and confidence thresholds? | After representative scanned document measurements |
| O03 | selfrag-v1 protocol validation | Design resolved by ADR-016; tokenizer/mask/reload probes and actual research results remain unrun |
| O04 | GPU hosting/runtime quantization choice? | After measured memory and latency on target hardware |
| O05 | Data retention, delete policy and PII handling? | Before handling confidential external files |
| O06 | Which datasets are licensed/accessible and suitable? | Before downloading/training/redistributing |
| O07 | Expanded comparison rules beyond grand_total_match_v1? | First rule fixed in EVIDENCE.md; currency conversion/tax/amendments remain later milestones |
| O08 | How to represent DOCX/XLSX source locators in viewer? | Before extended format release |
| O09 | Public user authentication vs intentionally local single-user demo? | Before internet exposure |

## ADR template for future changes

```text
## ADR-NNN: Short title
Date:
Status: proposed | accepted | superseded
Context:
Decision:
Alternatives considered:
Tradeoffs and risks:
Impact on source PDF comparison:
Affected docs, code, tests:
```

## ADR-011: Encoder-safe production chunks

Date: 2026-11-07

Status: accepted design; implementation/experimental gates pending.

Context: A 512-token chunk exceeds the embedder default input budget.

Decision: Use hybrid-v2: 240 content word pieces / 64 overlap, explicit model/tokenizer revisions and encoded-length assertions. Keep source-512-v1 only as an opt-in truncating historical experiment.

Alternatives rejected: Silent truncation or an unmeasured model swap.

Consequences: Smaller chunks may change context/recall; measure it and compare model variants within one retrieval profile.

Impact on source PDF: D17 changes the production chunk default; ranking stack remains.

Affected contracts and tests: docs/RETRIEVAL.md, docs/DATA_MODEL.md, ARCHITECTURE.md, IMPLEMENTATION.md, .env.example; TESTING scenario G.

## ADR-012: Extraction completion and search publication are separate

Date: 2026-11-07

Status: accepted design; implementation/experimental gates pending.

Context: The first native extraction slice precedes search, while the old ready state required indexes.

Decision: Use independent extraction/index states with derived preview_available/searchable and explicit latest/preview/published versions.

Alternatives rejected: One overloaded document ready flag.

Consequences: More explicit metadata, but failed indexing/reprocessing cannot erase a usable preview.

Impact on source PDF: Application addition, not a PDF algorithm change.

Affected contracts and tests: docs/DATA_MODEL.md, docs/INGESTION.md, docs/API_CONTRACT.md, docs/PRODUCT_SCOPE.md; TESTING scenarios A/G.

## ADR-013: Durable dispatch and matched index snapshots

Date: 2026-11-07

Status: accepted design; implementation/experimental gates pending.

Context: Crashes can lose a broker submission or publish only one retrieval branch.

Decision: Persist jobs and request keys transactionally; dispatch at least once, fence leased attempts, build immutable full workspace snapshots, then switch a PostgreSQL pointer after both branches verify. Deletion revokes access before cleanup.

Alternatives rejected: Assuming a distributed transaction or treating Celery delivery as durable application state.

Consequences: Full snapshot rebuilds cost storage/work; appropriate for the bounded local MVP. Incremental publication requires later measured design.

Impact on source PDF: Storage/recovery addition.

Affected contracts and tests: docs/INGESTION.md, docs/DATA_MODEL.md, docs/SECURITY.md; TESTING scenario G.

## ADR-014: Bounded independent evidence verifier

Date: 2026-11-07

Status: accepted design; implementation/experimental gates pending.

Context: A valid quote or full top-k does not establish support for a question.

Decision: Implement evidence-v1 identity/span/support checks, deterministic field/calculation claims and explicit abstention. Broader semantic verification is a measured later capability.

Alternatives rejected: Trusting critic labels, ranking scores or arbitrary fluent prose as verification.

Consequences: Initial QA coverage is narrower and honest; unknown claims are not certified.

Impact on source PDF: Strengthens DocuMind source support beyond the PDF demo.

Affected contracts and tests: docs/EVIDENCE.md, ARCHITECTURE.md, docs/API_CONTRACT.md; TESTING scenario H.

## ADR-015: Immutable source identities and scoped relational constraints

Date: 2026-11-07

Status: accepted design; implementation/experimental gates pending.

Context: Individually valid IDs can still form wrong-workspace/document/version references.

Decision: Use composite scoped FKs, immutable extraction rows, relational evidence memberships and validated JSON projections with raw Unicode source offsets.

Alternatives rejected: Independent resource/workspace FKs or mutable latest-version citation resolution.

Consequences: Additional constraints/joins prevent silent provenance corruption; authorization remains separate.

Impact on source PDF: Application provenance addition.

Affected contracts and tests: docs/DATA_MODEL.md, docs/SECURITY.md, docs/EVIDENCE.md; TESTING scenario I.

## ADR-016: Freeze selfrag-v1 as an explicit research adaptation

Date: 2026-11-07

Status: accepted design; implementation/experimental gates pending.

Context: Annotation and inference need identical token boundaries, masks and action semantics.

Decision: Adopt SELF_RAG_SPEC selfrag-v1 and critic-v1 with Continue/evidence reuse, one effective action per decision, bounded serial candidates, independent support checks and mandatory tiny probes before bulk labeling. V1 rejects repeated identical retrieval; query rewriting is deferred.

Alternatives rejected: Leaving the protocol open during annotation, or claiming exact paper beam-search fidelity.

Consequences: Greedy selection, conservative verification and one unique text query may reduce coverage; report these limitations. No trained capability exists until checkpoints and runtime gates pass.

Impact on source PDF: D18 declares custom tokens/masks/scoring and decoding differences; max retrieval-attempt budget remains 3.

Affected contracts and tests: docs/SELF_RAG_SPEC.md, docs/DATASETS.md, docs/EVALUATION.md; TESTING scenario J.

## ADR-017: Create queries once and replay events

Date: 2026-11-07

Status: accepted design; implementation/experimental gates pending.

Context: POST streaming and ambiguous synchronous/asynchronous results can duplicate generation on reconnect.

Decision: POST creates a durable run with an idempotency key and returns 202; GET polls or streams persisted sequences. Disconnect does not cancel; explicit cancellation races safely with finalization.

Alternatives rejected: Separate generation entry points for polling and streaming or exactly-once network delivery claims.

Consequences: Client must deduplicate replayed events; the database owns one terminal outcome, not one physical execution.

Impact on source PDF: D19 extends demo streaming into a durable API protocol.

Affected contracts and tests: docs/API_CONTRACT.md, docs/DATA_MODEL.md, docs/INGESTION.md; TESTING scenario L.

## ADR-018: Independent product gates and narrow comparison rule

Date: 2026-11-07

Status: accepted design; implementation/experimental gates pending.

Context: The dependency graph made product reporting wait for training and mixed OCR into native ingestion acceptance.

Decision: Split 1A native extraction / 1B OCR-tables; Phase 3 and Phase 7 release without training. Start exact grand_total_match_v1; defer fuzzy linkage, tax recomputation and amendment rules. Evaluate from the first slice.

Alternatives rejected: A single all-features release blocked by training or expanded business semantics.

Consequences: A useful product can ship earlier while clearly stating its supported questions and rules.

Impact on source PDF: D10/D19 clarify baseline-first independent releases.

Affected contracts and tests: IMPLEMENTATION.md, docs/PRODUCT_SCOPE.md, docs/EVIDENCE.md, docs/PROJECT_STATUS.md; TESTING scenario K and release gates.

## ADR-019: Single contract owners and explicit configuration status

Date: 2026-11-07

Status: accepted design; implementation/experimental gates pending.

Context: Duplicated enum/default definitions drifted; README linked to a nonexistent env template.

Decision: Assign owners in README, standardize partially_supported, provide a nonsecret proposed .env.example, correct paper section references and permit the supplied reference PDF/licensed fixtures while excluding private uploads.

Alternatives rejected: Duplicating full enums in every overview or treating a template as a working environment.

Consequences: Documentation checks can catch mechanical drift; application behavior still requires later runtime tests.

Impact on source PDF: No new algorithm change.

Affected contracts and tests: README.md, .env.example, IMPLEMENTATION.md, docs/CHANGES_FROM_PDF.md, docs/TESTING.md.

## ADR-020: Select EmbeddingGemma 2 and retain the remaining core models

Date: 2026-11-08

Status: accepted by user; runtime validation and immutable revisions pending.

Context: The user selected `google/embeddinggemma-2` for embedding and asked to fix the remaining model choices.

Decision: Use hybrid-v3 with text-only EmbeddingGemma 2, normalized 768-dimensional vectors and fixed SearchQuery/Document formatting. Keep `cross-encoder/ms-marco-MiniLM-L6-v2` for reranking and `meta-llama/Llama-3.2-3B-Instruct` for baseline generation and the future trained reflection adapter. The serving critic shares that adapter; evidence-v1 remains deterministic. Keep PaddleOCR/optional Docling for later extraction; exact OCR checkpoints require representative scan tests. External annotation teachers remain optional and disabled by default.

Keep 240/64 chunks measured with the pinned reranker tokenizer, check the full 512-token pair and separately check Gemma's 8192-token formatted input ceiling. Publish into a new 768-dimensional collection with complete encoding identity; old MiniLM profiles are historical experiments, never silent fallbacks.

Alternatives rejected: Retaining MiniLM as the default contradicts the user's choice. Increasing chunks to 8K breaks the retained reranker budget. Loading all modalities adds unused components to the text MVP.

Consequences: New embeddings and manifest/cache identities are required. Quality, latency, memory and dependency compatibility require actual tests; model selection does not establish superiority or readiness. Pin exact revisions during implementation rather than inventing tested hashes now.

Impact on source PDF: D20 replaces its dense embedder; hybrid ranking, reranker and Llama research base remain.

Affected contracts and tests: README.md, ARCHITECTURE.md, IMPLEMENTATION.md, .env.example, RETRIEVAL.md, DATA_MODEL.md, INGESTION.md, EVALUATION.md, TESTING.md scenario M, bootstrap prompt, source traceability and project status.

## ADR-021: Two inference connections with capability-gated Self-RAG

Date: 2026-11-08

Status: accepted by user as design; platform, security, trained release and runtime gates pending.

Context: The user wants DocuMind to manage inference in their Kaggle account, with a supplied generation endpoint when Kaggle is absent or unavailable. The proposed CPU app host is limited; the trained adapter needs its full base model but can be served through an API.

Decision: Adopt managed_kaggle and custom_endpoint connection kinds under INFERENCE.md. Managed Kaggle provisions a private complete trained runtime with explicit authorization. Ordinary custom endpoints provide baseline RAG without another local Llama; approved compatible trained endpoints provide Self-RAG. Use a FastAPI + Transformers/PEFT model-step API first, keeping policy, retrieval, critic ranking and independent verification in the app. The trained release is immutable and shared; document uploads never retrain it. Fully local deployment may run this same service without an external provider key.

A run pins connection/version, capability evidence and model/release identity. Quota/expiry/revocation causes explicit failure/unavailability; switching requires user selection and a new query. Credentials are owner/workspace-scoped, encrypted and never inherited from the developer account. Endpoint validation enforces SSRF defenses. Keep managed Kaggle disabled until its account access, platform permission, secret delivery, lifecycle and serving tests pass.

Alternatives rejected: An adapter-only local judge cannot run without its base. Ordinary external generation plus a separate local critic changes the current training contract and retains local compute cost. Silent provider fallback changes data destination and benchmark meaning. Ollama compatibility with the custom trained protocol is not established.

Consequences: Additional connection/version metadata, credential protection, model-step recovery and provisioning work. No promise of free always-on GPU, runtime throughput, API compatibility or completed Self-RAG. Build custom-endpoint baseline in Phases 2/3, trained service in Phase 6, managed deployment in gated Phase 6K; Phase 0/1A remains unchanged in scope.

Impact on source PDF: D21 adds deployment/provider choices and immutable serving releases; same-base research comparisons remain mandatory.

Affected contracts/tests: INFERENCE.md, ARCHITECTURE.md, IMPLEMENTATION.md, README.md, PRODUCT_SCOPE, API_CONTRACT, DATA_MODEL, SELF_RAG_SPEC, SECURITY, INGESTION, EVALUATION, TESTING scenarios N-P, .env.example, bootstrap prompt, source traceability and status.

## ADR-022: Implement the native slice with private filesystem storage and local access

Date: 2026-11-08

Status: implemented locally; public hosting and later capabilities remain gated.

Context: Phase 0/1A requires a durable native-PDF path before search or GPU inference. The attempted MinIO container image was unavailable from the registry. The bootstrap prompt permits a documented equivalent object store for local development.

Decision: Implement a private, atomic filesystem object-store adapter with a shared persistent Docker volume, backed by the same API-proxied source access contract. No public object URLs. Alembic creates only the native schema: workspace, document/version, durable job/request-key, page and element tables. PostgreSQL enforces scoped foreign keys and promoted-source immutability. Parser/config identity is pinned for every extraction; pages/elements promote in one fenced transaction. A subprocess bounds parser failure/time, and the container bounds worker memory. Redis/Celery is delivery; DB jobs and leases own recovery.

Local access uses one configured workspace and a generated private Bearer token, with Compose ports bound to loopback. This is not public authentication or multi-user account management. OpenAPI provides the initial inspection UI; Next.js remains Phase 3. Native nonblank text blocks are paragraphs, not inferred headings or table cells. Image-only pages require review; OCR is not silently substituted.

Add explicit idempotent reprocess and original-file download routes to make extraction versions and source inspection usable. Changed parser/config creates a new version; unchanged identity reuses matching work. Explicit retry requests have their own keys and never reopen work on key replay. Delete revokes access, cancels ingestion and performs leased cleanup; retain only metadata tombstones and request/job identity. Search indexes and inference tables arrive with later capabilities.

Alternatives rejected: A silent SQLite test substitute would not validate PostgreSQL constraints. Starting model/GPU work before native acceptance adds no evidence for this milestone. Blocking on one unavailable S3 implementation prevents the expressly permitted local storage slice. Exposing local-only services publicly is unsupported.

Consequences: Host development and Compose use different private object roots; do not interchange them against a populated database without migrating objects. S3/cloud storage needs its own adapter and integration tests before hosted release. Backups must cover DB and objects together. CI workflow is provided, but only observed local runs count as evidence. No model, retrieval, OCR, public auth or hosted throughput claim is implied.

Impact on source PDF: Native PyMuPDF extraction is retained; this is an implementation of the previously declared application storage/job/provenance additions, not a new Self-RAG algorithm deviation.

Affected contracts/tests: README, LOCAL_DEVELOPMENT, ARCHITECTURE, IMPLEMENTATION, API_CONTRACT, DATA_MODEL, INGESTION, SECURITY, TESTING, PROJECT_STATUS, environment template and native parser/storage/ingestion tests.

## ADR-023: Repository-owned persistent context with one owner per record

Date: 2026-11-08

Status: adopted for future repository tasks.

Context: The user requested context that persists across Codex sessions. Existing status and decision documents overlap with the requested root files, and AGENTS still described Phase 0/1A as work to start despite its recorded implementation.

Decision: Keep instructions in root AGENTS.md, durable facts/lessons in MEMORY.md, current status and dated validation in PROGRESS.md, and the complete ADR register in DECISIONS.md. Retain docs/PROJECT_STATUS.md and docs/DECISIONS.md as compatibility links only. README is the entry point; ARCHITECTURE/IMPLEMENTATION and the domain contracts continue to own design details. Before every task read the core context and relevant contracts/source. After meaningful work update only affected records in the same task, without a separate user request.

Alternatives rejected: Maintaining two full progress logs or decision registers creates competing versions. Putting volatile progress into AGENTS makes instructions stale. A new memory service, hook or generated script is unnecessary for this file-based workflow.

Consequences: Future agents must follow AGENTS for read-before-work and evidence-backed maintenance. Preserve dated decisions and substantive lessons; replace outdated current summaries and mark old snapshots historical. No secrets, private documents, unsupported metrics or claims that old tests were rerun. External automation is not installed; persistence comes from checked-in/workspace files and agents following the repository instructions. This workflow adds no application/runtime behavior.

Affected files: AGENTS.md, MEMORY.md, PROGRESS.md, DECISIONS.md, README.md, legacy pointers and references in active contracts. Historical ADR IDs and substance are preserved.

## ADR-024: Bounded local OCR, ruled tables and immutable human review

Date: 2026-11-08

Status: implemented; local verification and remaining deployment limits are recorded in PROGRESS.md.

Context: Phase 1B requires real scan text and table-cell provenance while preserving native preview, conservative quality states and older source anchors. The optional Docling layout path has no compatibility/quality evidence in this repository. A general document-layout model would add unmeasured runtime cost.

Decision: Keep the same leased PDF ingestion pipeline. Add an opt-in, CPU-only English PaddleOCR 3.7.0 / PaddlePaddle 3.3.1 adapter using PP-OCRv5 mobile detection and English recognition. Provision public models separately; record immutable repository revisions and actual file hashes in a manifest, validate local artifacts before work, and include identity/config in each extraction version. Use configurable low-text/image detection, 144-DPI OCR and existing resource/deadline budgets. Missing assets/runtime are explicit errors, not silent fallback. Default Compose stays native-only; the optional x86_64 OCR overlay installs dependencies and mounts the model directory read-only.

Use PyMuPDF native ruled tables and OpenCV complete raster grids, retaining table parents, row/column/span geometry, empty cells, raw offsets and header uncertainty. Native table headers are inferred; scanned headers and cross-page continuation are unknown until reviewed. All OCR/table results are needs_review regardless of confidence score: representative confidence calibration is absent. Do not infer business values or calculate amounts here.

Add an authenticated local review endpoint with explicit all-page confirmation and optional corrections to existing element text/header flags. A durable ingestion job builds a new immutable reviewed version with reviewer/base-element provenance; original versions and previews survive failure. Reviewed sources remain unindexed until Phase 2. No new UI or geometry editor is implied.

Alternatives: Docling/PP-Structure may later handle general layout, unruled/ragged/complex tables and continuation after compatibility/quality/resource tests. Tesseract or a prompt pretending to OCR would silently change the selected model. Overwriting extraction rows would break historical anchors. Automatically completing high-confidence OCR would pretend uncalibrated scores establish correctness.

Consequences: Native-only installation remains small; OCR adds substantial optional dependencies and separately stored weights. PDF-only, English and ruled-table limits are explicit. Clean synthetic scan/table checks establish real operation, not production accuracy or capacity on the proposed 2-vCPU host. Model cards declare Apache-2.0 for [detector](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det) and [recognizer](https://huggingface.co/PaddlePaddle/en_PP-OCRv5_mobile_rec); retain license obligations for redistribution. No model weights or credentials are source-controlled.

Affected contracts/tests: INGESTION, DATA_MODEL, API_CONTRACT, LOCAL_DEVELOPMENT, TESTING, Phase 1B gates, migration 0002/0003 and native/OCR/review regression fixtures. D02 remains the source-PDF deviation. Review fixes also preserve pagination identity, deletion rechecks and actual structured request emission; those correct existing contracts rather than change architecture.

## ADR-025: Local hybrid publication and bounded chat baseline

Date: 2026-11-08

Status: implemented and exercised locally. PROGRESS.md records the checks and deployment limits.

Phase 2 uses an explicit publication request to freeze the eligible PDF versions in one workspace. A worker builds complete replacement Qdrant and BM25 snapshots. PostgreSQL selects the new snapshot after matching membership checks and a compare-and-swap against the frozen parent. Keep retired snapshots for pinned queries and citations. Revoking a document invalidates any snapshot containing it and suppresses the results of runs that pinned it. Physical cleanup removes those index artifacts and the worker queues a replacement for surviving sources. This favors a simple, consistent local corpus over incremental indexing.

The text profile keeps the two selected checkpoints. The pinned Transformers loader removes the vision and audio towers and applies the checkpoint's masked mean and L2 normalization. This avoids the SentenceTransformers loader's multimedia processor dependency observed during real loading. Use 240 reranker content tokens and at most 64 overlap. Table chunks carry row anchors and header context, and evidence-v1 verifies only explicit paragraph fields on a resolved document.

The existing PostgreSQL job lease and Celery dispatch handle publication, query and synthetic endpoint validation jobs. A query pins the publication, effective document versions and any admitted endpoint version. One transaction commits the result, citation and terminal event. Because there is at most one optional chat call, a durable per-run dispatch marker records uncertain calls. Recovery fails the run if the call may have happened.

The local custom adapter uses nonstreaming OpenAI-compatible chat completions with write-only encrypted credentials, an operator-managed key, and DNS-pinned TLS for public endpoints. The operator can configure one exact loopback URL. Model output can match the independently verified answer, otherwise the verified field is returned with candidate rejection recorded. An omitted connection uses the verified template path. Failed or uncertain provider calls fail the run. JSON trace polling and cancellation are implemented. SSE and the web UI belong to Phase 3.

These choices keep the first slice executable with local storage and one worker. A paid provider, public multi-user identity, hosted secret storage and target-host capacity need separate deployment validation. No change to the source PDF's ranking algorithm is introduced beyond the existing ADR-020 replacement and budget decisions.

## ADR-026: Local workspace UI and replayable query events

Date: 2026-11-08

Status: implemented. Verification is recorded in PROGRESS.md.

Next.js provides the local browser workspace. Its route handler forwards to the operator-configured loopback FastAPI address and attaches the local API token on the server. The web server listens on loopback. API forwarding checks the Host, Origin and Fetch Metadata headers and rejects cross-origin requests. Endpoint credentials are entered in a password field and forwarded for encrypted backend storage. The browser receives sanitized connection versions and explicitly selects a validated version for each model-backed run.

Use the existing authorized PNG page renders for the PDF viewer. Citations resolve their stored extraction, page and element geometry, and the original PDF remains available through the same API. This reuses the canonical source model and avoids loading a second PDF renderer in the browser. The viewer highlights the cited element and shows its exact quoted span separately.

SSE subscribes to the durable query ledger with the existing GET events route. JSON polling remains supported. The browser reconnects using its last sequence and deduplicates events. Terminal events carry status and the result URL. The browser fetches answer text and citations through the authorized result route. Midstream source revocation emits a subscription error and closes the stream. This transient subscription error adds no terminal ledger event. SSE delivery may repeat and disconnecting leaves the job active.

The Phase 2 audit also requires an ordering between authorization and the first outbound write. Hold the existing workspace and job locks through the bounded request write, then release them before waiting for provider output. Deletion or revocation either commits first and blocks dispatch, or waits for the admitted write. This adds bounded contention during writes and does not recall upstream requests. Site-local IPv6 is explicitly rejected. Evidence-v1.1 retains unparsed competing field labels as uncertainty and rejects multiple invoice identities.

Migration 0005 retains each active document's last published extraction so a deletion-triggered replacement can keep surviving sources whose newer previews await review. It backfills from successful publication jobs. Readiness and retrieval use the same artifact verification. These fixes preserve the earlier contracts.

Next.js and React versions are pinned in the frontend lockfile. Plain CSS handles the bounded workspace layout. A Tailwind dependency is unnecessary for this slice. Browser tests use disposable backing data and a synthetic endpoint, while separate backend checks exercise a real chat model. Public authentication, managed Kaggle and trained Self-RAG remain later gates.

## ADR-027: Versioned SQL comparison with exact evidence

Date: 2026-11-09

Status: implemented in Phase 4. [Verification](PROGRESS.md).

Pin the authorized extraction versions when creating an investigation. Use the existing durable worker and a bounded paragraph grammar to store typed records and exact field anchors. The first rule resolves one invoice and one PO by explicit matching identifiers. Common explicit currency and tax basis are required. Competing sources or uncertain fields yield a needs-review outcome. Use the separate investigation API so comparisons can run before search publication and with model inference disabled.

Store totals as `numeric(20,6)` and the Decimal difference as `numeric(21,6)` to handle opposite-sign inputs. Scope the evidence foreign keys to the pinned versions and enforce the raw Unicode slice in PostgreSQL. Result reads verify the evidence and recompute the difference from the typed inputs. The result opens both grand-total anchors through the existing source viewer.

Use one immutable comparison and one CSV per investigation. The CSV is small enough to store with its checksum and job state in PostgreSQL. Verify it again on download. Deletion revokes the investigation, clears source-derived records and report bytes, and retains safe job and investigation metadata. Additional rule types and richer reports remain separate milestones.

A Phase 4 follow-up found competing fields could be hidden by paragraph-only parsing. Table-cell labels now block a verified finding. Read-side checks reject affected legacy findings and downloads so new investigations can record the safe outcome.

Report creation and idempotent replay check the same finding. Ready metadata verifies the rendered content and checksum before advertising a download.

## ADR-028: Keep offline reflection experiments separate from approved releases

Date: 2026-11-09

Status: implemented for the pilot pipeline. Target Llama release gates remain open.

Freeze synthetic pilot data by source identity, group and template with independent manifest and serializer hashes. Use the same pinned chat-template serializer for offline training and the later runtime. Save only PEFT and selected token rows alongside the tokenizer and token map. Verify both embedding and output rows after reload and record model-issued decision IDs separately from policy and family-constrained metrics.

Run the CPU smoke with explicit FP32 on pinned open SmolLM2. Keep the CUDA NF4 probe labeled as an open-model smoke and retain the exact Meta Llama target for the research release. Every offline bundle is experimental until the adjudicated data, target checkpoint, evaluation and runtime gates pass. [Training runbook](docs/LOCAL_DEVELOPMENT.md#phase-5-offline-reflection-training).

## ADR-029: Review the frozen test split against an exact training bundle

Date: 2026-11-09

Status: implemented for offline review.

Score validation during tuning and require explicit test scoring for a selected checkpoint. Record raw decisions separately from family-constrained scores so illegal model output remains visible. Review case packets verify the corpus, predicted label positions, artifact files and independently recorded manifest SHA. Record human case notes separately from the immutable bundle and preserve the experimental status. This makes review reproducible when the target checkpoint becomes available. [Training runbook](docs/LOCAL_DEVELOPMENT.md#phase-5-offline-reflection-training).

## ADR-030: Operator-pinned reflection runtime and scoped application loop

Date: 2026-11-09

Status: implemented for the gated API and private runtime. Approved release validation remains open.

Keep the model service separate and load one exact PEFT bundle. Validate the manifest, tokenizer, trained rows and real decision probe before serving. Admit the target only through operator configuration, an independently pinned digest and reviewed target corpus. Open-model pilots expose their own capability. Reuse the DNS-pinned HTTPS or literal loopback transport and hold authorization through the first write of each step.

Pin runtime instance and source scope in the durable query. Use the existing labeled-field verifier and conservative unscoped general allowlist. Preserve runtime step replay within the live service. The application fails closed after worker lease loss following a model dispatch until confirmed-step recovery is implemented. [Runtime runbook](docs/LOCAL_DEVELOPMENT.md#phase-6-private-reflection-runtime).

## ADR-031: Pin open Qwen3-4B-Instruct-2507 for the trained release

Date: 2026-11-09

The configured registries denied access to the original Llama base. The user authorized a comparable available model. Select `Qwen/Qwen3-4B-Instruct-2507` at `cdbee75f17c01a7cc42f958dc650907174af0554`. Qwen publishes the 4B non-thinking model under Apache 2.0 and reports strong instruction following. We verified an unauthenticated weight-shard range request and all 15 reflection token round trips. [Official model card](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507).

Qwen is larger than the 3B target, so measure its GPU footprint and throughput on the actual run. A smaller thinking-mode base would need explicit control of additional reasoning behavior. Preserve PEFT NF4, the episode grammar and the existing retriever. Retrain adapters and compare against the same Qwen revision for controlled baseline measurements. Previous SmolLM2 and Llama artifacts retain their model identities. This supersedes the generator selection in ADR-020.

## ADR-032: Resume reflection from private confirmed checkpoints

Date: 2026-11-09

Checkpoint validated step responses and ordered retrieval IDs under the same lease as the public trace. A replacement worker reconstructs the prior state, matches the original step hashes and consumes the existing trace prefix. This preserves budgets and prevents duplicate model calls, searches and terminal events. A healthy worker renews the lease while waiting. Ambiguous external outcomes still terminate the run, and the temporary journal is cleared on every terminal or revoked path.

## ADR-033: Test Kaggle inference through an owned private Jupyter session

Date: 2026-11-09

The user authorized a private Kaggle serving test. Kaggle exposes private Jupyter sessions for external ML execution. The SDK returns an operation with a session ID and, when ready, a credential-bearing Jupyter gateway URL. Use this supported route for the bounded operator prototype. Upload only allowlisted source modules and a hash-checked experimental bundle, execute the fixed bootstrap with history disabled, then test authenticated HTTP model steps through the loopback-port proxy. [Kaggle notebooks](https://www.kaggle.com/docs/notebooks) · [Official SDK](https://github.com/Kaggle/kaggle-sdk-python/blob/main/kagglesdk/kernels/services/kernels_api_service.py).

The Jupyter URL is an execution credential, including its path. Store it only in private local session state. The runtime bearer token is separate, injected through the authenticated execution channel and retained in memory. Bind lifecycle actions to the exact notebook owner, source digest, version and returned session ID. Persist create intent before allocating a GPU and fail closed when its outcome cannot be recovered.

Keep this operator tool outside the public connection API. Application admission still requires the approved model release and per-owner connection integration. Record cancellation acceptance, gateway revocation and GPU cleanup as distinct observations. A stopped notebook kernel or an inaccessible gateway alone cannot establish GPU release.

## ADR-034: Mask unsupported answer words and test copied values with opaque evidence

Date: 2026-11-09

The previous synthetic corpus trained the generator to imitate incorrect answer words in negative examples. Its IDs also exposed fixture variants. The model could achieve perfect gold-prefix support-label scores while emitting incorrect dates and currencies with full-support labels.

Use `unsupported_answer_words_masked_v1` during training. Keep unsupported answer tokens as input for critic supervision, mask their ordinary-token loss, and continue supervising supported answer text and reflection labels. Record the policy and validator hashes in every new experiment. Preserve the byte-identical inference serializer and old frozen corpora and bundles.

Introduce synthetic-v3 with opaque UUID anchors, paired changed values, multiple layouts and an independent grouped holdout. Score actual greedy answer text and false full-support claims with the same evaluator for previous and new adapters. Select weights using validation only. These fixtures are limited synthetic evidence, and checkpoint-bound human acceptance remains a release gate.

## ADR-035: Fit explicit source windows with the admitted generation tokenizer

Date: 2026-11-09

The retriever's 240-token chunks can exceed the selected Qwen bundle's 512-token context after serialization and its 132-token candidate reserve. Byte limits and reranker token counts cannot establish generation capacity.

Cache the exact pinned generator tokenizer on the app host and verify its IDs, vocabulary, native special IDs and chat-template hash against the runtime. Preflight every complete request and check returned input counts, including confirmed replay. For an oversized bounded-field passage, select and trace a contiguous raw window retaining the complete field line and exact adjusted offsets. Verify claims against the entire pinned source and keep question/committed claims intact. Unfittable requests finish locally with `context_limit`.

This preserves immutable checkpoint hashes and the training serializer instead of increasing a release's context limit without validation. It adds a tokenizer-only app dependency, provisioned through an explicit setup flag. Baseline setup stays independent. The required capability field needs an app/runtime upgrade together. Regression decoders with the actual Qwen tokenizer establish sizing and source integrity, not target-model quality or GPU capacity.

## ADR-036: Bind recorded human review to actual generated outputs

Date: 2026-11-09

Conditional label predictions conceal wrong freely generated answers. The old recorder also emitted derived fields that the runtime's strict submission schema rejected.

Require a separate greedy test artifact bound to the immutable bundle and frozen corpus. Export its actual answer, critic labels, finish and recomputed outcome with each evaluated episode. Mark omitted contexts explicitly as unrun. Human submissions bind the generated artifact hash. A distinct recorded-review schema adds the corpus binding, counts and experimental status. The runtime consumes that recorded file and recomputes every binding/count. Reject accepted false-full-support cases, altered records and records containing rejected cases.

This requires fresh review of older gold-only submissions and retention of one additional evaluation artifact. It does not turn gold-context diagnostics into application evaluation, approve a bundle automatically, or replace the later deployment gate. No human acceptance was created by the remediation.

## ADR-037: Cancel the recorded Kaggle session independently of notebook edits

Date: 2026-11-09

Strict current-source/version checks made Stop reject an already provisioned session after ordinary notebook edits. Requiring the notebook to remain unchanged can prevent resource cleanup.

Retain those template checks for start/poll. Stop checks the configured account against the recorded owner, validates the exact persisted session/creation-operation binding and cancels that session without reading mutable notebook metadata. The operator Stop helper requests cancellation before gateway observation and never requires a notebook poll first. This also permits cleanup after privacy changes or notebook deletion. Owner changes and mismatched session/operation IDs fail closed. The private state remains the creation record.

This refines ADR-033's lifecycle checks only for cleanup. It does not authorize cancelling unrelated resources. SDK acceptance, gateway revocation and GPU deallocation remain separate observations; unknown creation and lifetime cleanup are still open audit work.

## ADR-038: Bound operator sessions and preserve creation receipts for recovery

Date: 2026-11-09

Allocation errors escaped the cleanup block, and an HTTP-only timer left the model process alive. Persist a 20-minute expiry before creation, retain the operation response before metadata validation, and include Start in the runner's cleanup/failure journal. Never repeat an ambiguous create. Reconcile retained responses without allocating again. With no response, an operator may supply the independently confirmed creation operation and exact notebook ref; record that identity as operator-confirmed rather than API-proven notebook linkage.

Launch a detached owner-scoped cancellation watcher before allocation. It retains the original deadline, survives command exit and reconciles exact-session cancellation. Status after expiry cancels before checking mutable notebook metadata. A separate remote guardian bounds the entire model subprocess group, including installation/loading, so HTTP shutdown cannot retain its weights in the notebook kernel. Tokens enter the child environment and are absent from its saved program. Cancellation and gateway revocation are idempotent.

The watcher requires the operator host to stay available. On host restart, status/reconciliation uses the persisted expiry; unknown outcomes may still need operator identification in Kaggle. The remote guardian bounds model-process lifetime independently, but that alone cannot establish platform GPU deallocation. Keep product managed Kaggle disabled. This refines ADR-033/037; the live application run observed independent cancellation and later gateway revocation, while physical deallocation remains unknown.

## ADR-039: Retain uncertain query submissions until receipt or explicit replacement

Date: 2026-11-09

A lost accepted response made Ask generate a second key. Store the original question, document scope, destination/config version and key until acceptance is known. Retry that exact operation even if the current selection changes. Freeze editing while uncertain, and make starting a separate query explicit; the old accepted run may still finish. Guard concurrent sends and preserve backend idempotency instead of adding another execution route.

The browser regression accepts the first request in the backend, drops its response and verifies the retry returns the same run ID with identical key/body. Cached answers and source highlights also clear when the backend requires evidence rechecking. This is in-memory recovery for the current page; it does not promise browser-restart persistence or cancellation of a run whose receipt is unknown.

## ADR-040: Require real adapter CI evidence and isolated selected-model application evaluation

Date: 2026-11-09

CI now installs training dependencies, trains the pinned public CPU smoke base against the committed frozen corpus, saves/reloads all 15 trained rows and requires that artifact in the authenticated runtime test. Bound training at 120 steps and ten minutes. Missing required artifacts fail instead of skipping. The equivalent local CPU train/reload/runtime sequence passed; remote execution of this unpushed workflow is not claimed.

Evaluate the selected immutable Qwen adapter through actual PDF upload/parsing, publication, hybrid retrieval, durable query jobs, GPU model steps and citation verification. Use disposable stores and privately retained synthetic inputs/results. The operator harness explicitly injects experimental admission and private-gateway transport while preserving the real `reflection_pilot` capability. It does not enable production admission or fabricate human approval. `evidence-v1.3` adds only the exact frozen invoice paraphrase for total/currency/due date; preserve the corpus, model weights and selfrag-v1 serializer.

The measured 46-case run returned 43 expected outcomes, zero false verified answers and 26 resolvable scoped citations. A tail-field window lost the distant invoice identifier and was rejected by relevance; two arithmetic questions prematurely emitted Finish. Preserve these failures. Improve context/identity handling and eligible-task training using fresh grouped data before another release evaluation; do not tune on this used holdout or label the result production-ready.

## ADR-041: Preserve invoice identity across distant fields and validate eligible addition

Date: 2026-11-09

The reviewed long invoice contained both its ID and the requested total. The 512-token trained runtime could fit the complete total and still lose the ID from the candidate context. Provide two independently anchored excerpts when a complete named-invoice field and exactly one matching identity label occur in the same pinned paragraph. Keep the field inside the original retrieved chunk, preserve both raw offsets and fit the complete serialized context. The runtime advertises and validates the span limit. The verifier still sees the full scoped source and blocks conflicting or table fields. This avoids fabricating a contiguous quote or changing the selected checkpoint's budget and serializer.

The v3 eligible training set used reference repetition. V4 adds distinct integer addition trajectories with disjoint unordered operand pairs across splits and reserves both observed misses for regression. The trained model still emits its own actions and answer. For a recognized addition, an initial empty `[Finish]` gets one candidate attempt without retrieval. Log the raw choice and the override. Accept the generated candidate only when it equals the exact integer sum. Report raw early Finish separately from recovered application outcomes. The fresh holdout, original regressions and operator release review have separate roles.

## ADR-042: Local-only application with independent reflection and generation

Date: 2026-11-11

Status: accepted from the user's architecture change. Runtime and quality verification are tracked in PROGRESS.

Run the application, documents, metadata, indexes, embedding, reranking and verification on the user's device. Remove public application deployment and SaaS account management from the roadmap. Hugging Face remains a model registry. Keep local bearer access, workspace scoping, private object permissions and idempotent durable jobs.

The fine-tuned reflection model runs either on the device or in a private user-owned Kaggle session. Answer generation is independently selected from a local server, private Kaggle bridge or supplied endpoint/model/key. The reflection role includes the matching base and adapter. It makes trained retrieval decisions and scores the exact candidate produced by the selected generator. Support and utility labels are conditioned on that candidate using the existing trained grammar. Independent source and arithmetic checks remain authoritative.

Retain the integrated checkpoint strategy for compatible saved runs and same-base research. New split runs pin both runtime identity and the generator's immutable connection/version/capability check. Record the strategy and actual inference profile. Preserve the serializer and earlier step hashes by placing optional candidate text in the bounded wire request. Journal provider intent before dispatch, save its exact candidate, and resume only fully confirmed steps.

Use explicit local commands for Kaggle provisioning and an authenticated loopback relay that exposes inference routes only. Keep Jupyter execution credentials in owner-only local state, separate from the model bearer token. Existing fixed expiry, independent watcher, process guardian and exact-session cleanup remain required. This replaces browser-based hosted account provisioning with the local operator workflow.

The local model service can load CPU/MPS floating-point profiles or the checkpoint's CUDA profile. Hardware capacity and numerical/critic quality require evaluation for each profile and generator pairing. The split architecture is a declared Self-RAG-inspired adaptation. The integrated same-base controls remain necessary for research attribution.

Alternatives considered were requiring a hosted complete trained generator for every answer, exposing notebook execution endpoints to the UI, or pretending ordinary provider output contains trained reflection labels. The selected design keeps the existing contracts and stores while adding explicit candidate scoring, a local generator service and private model bridges.

## ADR-043: Score selected checkpoints without changing their immutable bundles

Date: 2026-11-15

The selected v4 bundle correctly contains validation-only conditional predictions. Requiring test predictions inside that already frozen bundle prevents later review unless its manifest is changed. Keep the bundle and weights unchanged. The evaluator's `--conditional` operation loads and checks the exact checkpoint, then reuses the trainer's gold-prefix encoder/scorer without training. Save the test scores separately and embed them in a fresh generated-evaluation artifact alongside the original greedy outputs.

The review packet accepts this supplemental evaluation only for a bundle that explicitly omitted test scoring. Require matching checkpoint/corpus hashes, the test split and complete consistent held-out predictions. Reject missing scores, malformed rows, wrong bindings and attempts to replace scores already shipped in a bundle. The recorded review binds the complete generated-evaluation digest, including the supplement. Existing bundles and recorded reviews remain consumable. This avoids retraining or repacking solely to calculate metrics and preserves the distinction between conditional predictions and actual generated answers.

## ADR-044: Identify delegated agent review honestly

Date: 2026-11-15

For this Phase 6 validation, the user explicitly authorized Codex to validate whatever is needed on their behalf. Record any resulting case adjudication under Codex's own delegated-agent identity, using actual checkpoint-bound outputs and original sources. Do not attribute these verdicts to `here-2007`, invent human participation or reuse the human v3 review for v4.

This delegated operator review can support the authorized local experimental runtime validation. It does not constitute human evaluation, human-verified training labels, a blinded study or a representative research release. Preserve the existing minimum 20 distinct cases, both retrieval policies, source inspection, complete score/output bindings and false-full-support rejection. Records retain `experimental_unapproved`; default application inference stays disabled unless explicitly configured. Phase 8 research/human evaluation remains independent. Future agents need explicit delegation for their own adjudication; this decision is not blanket approval for later checkpoints.

## ADR-045: Bound workspace uploads and recover comparison submissions

Date: 2026-11-15

The per-file size limit leaves the local collection unbounded. Count documents that have not completed deletion under the existing PostgreSQL workspace lock. Check the accepted idempotency receipt first, then apply the configurable 200-document default to new uploads before writing any data. This uses the document lifecycle as the quota source and avoids a separate counter. Existing data remains readable if the operator lowers the limit. A confirmed browser deletion revokes dependent results immediately and frees a slot after cleanup.

Use the existing investigation idempotency contract when the browser loses the create receipt. Retry the same selection with the same request key, and expose the saved draft once accepted. Apply the finding verifier to evidence links as well as findings and CSVs, and recheck open views on refresh. Keep one source-cited CSV per investigation and retain the exact rule and pinned versions from ADR-027.

## ADR-046: Separate artifact admission, send reservations and worker leases

Date: 2026-11-15

Keep the configured upload limit for new admissions. Save accepted original sizes alongside their existing digest and save size and SHA-256 for new renders. Give matching upload replays a bound derived from the accepted size, with a bounded multipart allowance. Before using a legacy original's size, verify its stored digest. Legacy previews use structural validation and saved geometry because historical render digests are unavailable. This avoids eager rewriting of user objects. A valid but substituted legacy PNG cannot be compared with a historical digest.

Reserve a queued send atomically for 60 seconds before attempting broker submission. A dispatcher crash or uncertain result can delay another send by up to that interval. Workers can claim immediately after delivery, and the existing worker lease owns processing and fencing. A transient cleanup failure consumes the usual processing retry budget. If it runs out, keep the original cleanup job visible for retry and keep the quota slot occupied until cleanup succeeds. Source and report revocation still happen at deletion acceptance.

Preserve the creation key until a comparison receipt is known. Once accepted, retain the returned ID across detail-read failures so a retry can recover the saved investigation.

## ADR-047: Pair Phase 8 models on the same pinned base and keep cohorts separate

Date: 2026-11-15

The available pre-training v4 test split contains 60 cases. Keep it intact and measure it separately. Freeze the new 200-question grouped synthetic set before the first paired evaluation. Label it post-training and preserve all run failures. Do not retune the prompt, rankings or adapter on scored outcomes. Representative human-adjudicated evidence and a 200-case pre-training holdout cannot be obtained retroactively for the selected checkpoint.

Use the same Qwen revision, NF4 profile, hardware, scope and output token cap per pair, with single-request execution. The baseline uses the unadapted base for one completion. The trained arm uses the v4 adapter and application reflection grammar. This measures the combined adaptation and control-loop effect. To attribute support-critic behavior, keep the trained weights, retrieval and independent verifier fixed and remove only the support gate and support rank term. Reuse exact matching step prefixes for that ablation and exclude its latency. Compare retrieval components on a frozen publication with workspace-wide queries. Keep research controls in the offline harness without changing production defaults.


## ADR-048: Fence physical artifacts and keep uncertain connection submissions recoverable

Date: 2026-11-16

The deep audit reproduced direct Qdrant access, a staged page recreated after deletion, runtime work surviving application failure, unbounded cleanup lock waits, stale-schema readiness and duplicate connection saves. Keep the existing local architecture and close these boundaries in the shared paths.

Require a separately generated Qdrant key in the server and client, disable CORS and retain loopback publication. Existing configuration can append only a missing key through an explicit initializer upgrade. Preserve all other settings and the existing volume. Check the installed Alembic revision against this application's migration heads before repository-backed HTTP operations and readiness. Return a migration instruction without changing the database automatically.

Use one session advisory-lock helper for publication artifacts and document writes/cleanup. Acquisition is nonblocking and reuses the existing bounded job retry policy. Renew the ingest lease after acquiring its document lock and hold cleanup's document lock until its physical sweep and receipt commit finish. Preserve the short final promotion transaction and its visibility/lease fence. This prevents a stale physical write without holding a long workspace transaction. Contention can still exhaust retries and require the visible cleanup job to be retried.

Give each reflection call its own bounded deadline and persist it for exact confirmed replay. Retain the original deadline interpretation for older journals. Attempt exact-step cancellation on transport failure and preserve the original failure even if cancellation fails. The runtime checks its deadline during decoding; an executing device kernel remains cooperative. Always kill the guardian-owned process group when its direct child exits.

Keep the browser connection save's exact body and request key in transient memory until confirmed. Freeze edits during an unknown outcome. Reuse the original expected version for PATCH replay. A later client rejection cannot establish that an earlier uncertain write failed. No credential goes into browser storage. Reload loses transient recovery state, so inspect the saved connection list before creating again.

Alternatives were waiting on locks without lease renewal, relying on orphan collection after a deletion receipt, treating local ports as authentication, or issuing a new save after receipt loss. These fail the reproduced orderings. The Qdrant settings match its [pinned configuration](https://github.com/qdrant/qdrant/blob/v1.13.4/config/config.yaml). Validation and outstanding findings belong in PROGRESS.md.


## ADR-049: Bind Phase 8 reports to measured inputs and constrain file processing

New benchmark runs declare their run kind and record all paired outcomes. Retain a copy of the code covered by the run and require a matching trusted checkout when reporting. Each paired shard also binds runtime evidence to its config and wire outputs. Keep shards with distinct code or model identities separate. Historical replay is limited to archived config and result digests already recorded in the November 15 benchmark. This supersedes the workspace-wide retrieval rule in ADR-047 because some frozen questions need their selected document to determine the gold span.

Use the frozen case scope for each retrieval variant, retaining BM25 publication statistics to match production rankings. Score the first nonempty generated candidate before filtering and retain candidate dispositions separately from the committed reflection text. This permits recomputing raw behavior without changing the original final answer scoring.

Restrict PDF processing before touching the input. The child gets a minimal environment, a private work directory and read-only runtime/model paths under OS confinement on the supported local platforms. Fail closed when the sandbox is unavailable. The worker retains its original job, heartbeat and promotion rules.

Require immutable Hub checkpoints, flat safetensors shard names and regular files inside the pinned cache before the bundled generation and reflection loaders call Transformers. This addresses the advertised checkpoint input path while waiting for a verified upstream Accelerate fix. Promote the fixed pytest dependency into the locked environment.
