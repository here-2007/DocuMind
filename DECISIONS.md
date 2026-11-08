# Architecture decision record (ADR) register

This is the canonical architecture and repository-workflow decision register. Each entry distinguishes planned decisions from implemented behavior; current execution status and validation belong in [PROGRESS.md](PROGRESS.md). If a decision changes, append a new ADR rather than rewriting history. Significant deviations from the source PDF must also be recorded in [docs/CHANGES_FROM_PDF.md](docs/CHANGES_FROM_PDF.md).

Current authority: ADR-024 defines the bounded Phase 1B extraction/review implementation; ADR-023 defines context-file ownership and maintenance; ADR-022 records the implemented local native slice; ADR-021 defines managed Kaggle/custom-endpoint inference, permits explicit external baseline generators and refines ADR-007/020 deployment boundaries; ADR-020 selects EmbeddingGemma 2 and supersedes ADR-011's production embedding/tokenizer profile; ADR-011 supersedes ADR-004's production chunk size; ADR-016 resolves ADR-009's proposed grammar as an explicitly adapted protocol; ADR-018 refines ADR-003's release dependencies. Earlier entries remain as history, not competing active defaults.

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
