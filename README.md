# DocuMind AI

**Self-RAG Powered Multimodal Document Intelligence and Cross-Document Reasoning Platform**

DocuMind runs locally on your device. Choose the fine-tuned reflection model on your device or in your private Kaggle session. Choose answer generation independently on your device, Kaggle, or an API endpoint with your key. Embedding, reranking, document storage and validation stay local. [Model setup](docs/LOCAL_DEVELOPMENT.md#local-only-model-setup).

**Status.** The local app supports PDF extraction, hybrid search, source-cited baseline QA and exact invoice/purchase-order grand-total comparisons. The Phase 7 comparison and CSV release passed local acceptance with inference disabled. The Next.js workspace shows replayable traces, cited findings and downloadable CSVs. Native text, optional English OCR, ruled tables and immutable review feed the same backend. [Workspace setup](docs/LOCAL_DEVELOPMENT.md#phase-3-workspace) · [Comparison setup](docs/LOCAL_DEVELOPMENT.md#phase-4-deterministic-comparison) · [Verification](PROGRESS.md).

Qwen v4 passed delegated operator review for opt-in experimental use with private Kaggle reflection. The tested profiles matched 73/73 integrated cases and 72/73 split regression cases, with zero false verified answers. One adversarial split case safely abstains. Clean local setup passed with verified cached weights. The trained FP16 MPS profile timed out on the tested 16 GB Mac. [Release and setup evidence](docs/validation/local-release-2026-11-15.json) · [Current verification](PROGRESS.md).

Phase 8 measured the same pinned Qwen base against integrated trained Self-RAG. On the broader 200-case synthetic set, baseline scored 86% and Self-RAG 81%, with median query times of 1.42 s and 6.18 s. Every emitted citation matched gold evidence, with recall of 91.67% and 73.33%. The older 60-case pre-training cohort scored 75% and 100%. Disabling support-critic filtering and scoring changed zero final answers. These controlled results retain the trained model's relevance refusals and broader coverage misses. The 200-case set was created after training. Human review and representative research validation remain open. [Results, ablations and limitations](docs/validation/phase8-benchmark-2026-11-15.md).

## Start here (for developers and Codex)

Read [`AGENTS.md`](AGENTS.md), then the project documents in this order:

1. [MEMORY.md](MEMORY.md) and [PROGRESS.md](PROGRESS.md).
2. Relevant decisions in [DECISIONS.md](DECISIONS.md), then this README.
3. [`docs/CHANGES_FROM_PDF.md`](docs/CHANGES_FROM_PDF.md).
4. [`docs/PRODUCT_SCOPE.md`](docs/PRODUCT_SCOPE.md).
5. [`ARCHITECTURE.md`](ARCHITECTURE.md).
6. [`IMPLEMENTATION.md`](IMPLEMENTATION.md).
7. The relevant authoritative contracts listed below.

Original source: [`reference/original_self_rag_spec.pdf`](reference/original_self_rag_spec.pdf), 7 pages, titled *Self-RAG Project Spec*. In this project the original architecture is the **research backbone**, not the full application specification. Anything the original PDF does not define is either an explicit DocuMind design decision or a proposed implementation detail, not a claim about the source.

## System Overview

This blueprint covers the local PDF, retrieval, baseline QA and structured comparison backend plus the planned product. OCR, indexing and endpoint generation are opt-in. The [runbook](./docs/LOCAL_DEVELOPMENT.md) lists the available routes and the [project status](./PROGRESS.md) records verification.

A workspace where a user uploads business documents (including scanned PDFs), asks evidence-based questions across them, identifies inconsistent facts or amounts, and receives an answer with document/page/element citations and an auditable retrieval trace.

**Signature demo.** Upload one invoice and its purchase order. Open **Compare documents**, select both PDFs, and run the comparison. It checks the explicit PO and vendor IDs, currency and total basis, then shows the exact grand-total difference and the highlighted source lines. Open both sources and export the cited CSV. Ambiguous records produce a reason for abstention. [Demo setup](docs/LOCAL_DEVELOPMENT.md#phase-4-deterministic-comparison).

## Global Invariants

The executable local app enforces scoped database references, local Bearer access, immutable sources, bounded parsing and fenced jobs. Retrieval/model and later product acceptance are tracked in [PROGRESS.md](PROGRESS.md). The following requirements come from [AGENTS.md — Nonnegotiable rules](./AGENTS.md#nonnegotiable-rules):

- **Evidence over fluency.** Never fabricate a citation, calculation, OCR result or metric.
- **Deterministic work stays deterministic.** Python/SQL handle sums, mismatches and currency rules; the LLM explains findings.
- **Uploaded document questions require retrieval.** Self-RAG can skip retrieval only where doing so does not sacrifice private, changing or document-specific evidence.
- **Preserve source provenance.** Every searchable chunk can be traced to its original document and page; every supported claim traces to one or more chunks/elements.
- **Research claims require evidence.** 'Self-RAG' is a trained reflection-token decision process, not a prompt that prints tags.
- **Ship in vertical slices.** A functioning baseline document QA system must exist before attempting expensive QLoRA training.

## Target stack

| Area | Technology | Role |
|---|---|---|
| Web | Next.js, React, TypeScript, CSS | Document viewer, chat, connection setup, trace |
| API | FastAPI, Pydantic | Document and question workflows, streaming events |
| Ingestion | PyMuPDF, Docling, PaddleOCR, OpenCV (as needed) | Native text, structure, OCR, tables |
| Storage | PostgreSQL, object storage, Qdrant | Structured metadata, originals, vectors |
| Jobs | Redis and Celery | Asynchronous parsing and indexing |
| Search | `google/embeddinggemma-2`, `rank_bm25`, RRF, `cross-encoder/ms-marco-MiniLM-L6-v2` | Dense + sparse + reranking |
| Generation | Qwen3-4B-Instruct-2507, PyTorch, Transformers, PEFT, bitsandbytes | Baseline and trained reflection-token inference |
| Evaluation | pytest, RAGAS, custom scoring, pandas | Reproducible comparison and audit |
| Application execution | Local processes and Docker Compose | UI, API, workers and stores stay on the user's device |

This stack is a **target**, not a claim that every dependency works on every CPU/GPU. See `DECISIONS.md`.

### Selected model roles

| Role | Selected model / implementation |
|---|---|
| Embedding | `google/embeddinggemma-2`, text-only, 768 dimensions; hybrid-v3 in RETRIEVAL.md |
| Reranking | `cross-encoder/ms-marco-MiniLM-L6-v2` |
| Baseline generation | Controlled model `Qwen/Qwen3-4B-Instruct-2507`. Other supported external endpoint models require recorded identity |
| Trained reflection | Pinned Qwen base and adapter for retrieval decisions and candidate criticism, on this device or private Kaggle |
| Independently selected generator | Local, private Kaggle or an OpenAI-compatible external endpoint. Integrated Qwen remains available for controlled research |
| OCR / tables | CPU PaddleOCR 3.7.0 / PaddlePaddle 3.3.1 with PP-OCRv5 mobile detection + English recognition; PyMuPDF/native and OpenCV/scanned ruled tables. Docling/general layout remains deferred |
| Claim/citation verification | Deterministic evidence-v1 checks; no additional model |

Executable loaders and experiment manifests pin model/tokenizer revisions and dependencies. Experimental checkpoints still require release approval; current validation is in PROGRESS.md. External annotation teachers remain optional and separately configured. ADR-020 records the embedding change; ADR-021 defines the inference connections; ADR-031 pins the open Qwen generation base.

### Inference connection options

1. **Fine-tuned reflection model:** run the checked model service locally, or start a private Kaggle session with the local bridge command.
2. **Answer generator:** select a local server, private Kaggle bridge, or external endpoint/model/API key in the workspace.

The split path lets the trained runtime evaluate the selected generator's exact candidate. Each run records both identities and applies independent evidence checks. Model outages require an explicit choice for a new run. The app has no public deployment target. [Execution contract](docs/INFERENCE.md) · [ADR-042](DECISIONS.md#adr-042-local-only-application-with-independent-reflection-and-generation).

## Interface

- **Implemented interface.** The Next.js workspace covers upload, source review, publication, chat, citations, endpoint setup and invoice/PO investigations with CSV export. The API supports SSE and JSON trace replay. See the [runbook](./docs/LOCAL_DEVELOPMENT.md#phase-3-workspace).
- **Configuration.** `INDEXING_ENABLED` and `GENERATION_ENABLED` activate the local Phase 2 paths. Configure endpoint credentials through the scoped API and keep `INFERENCE_SECRET_KEY` private. See [the environment template](./.env.example) and [security contract](./docs/SECURITY.md).
- **Developer instructions:** [AGENTS.md](./AGENTS.md) defines implementation requirements and the required reading order. The documentation index below provides the current human/agent navigation surface.

## The first demo that counts as working

Upload one native-text PDF, finish an ingestion job, ask a question, get an answer grounded in retrieved passages with a clickable page citation and a visible retrieval trace. Then support two documents and the exact invoice/PO grand-total comparison. Both product releases are independent of training. Contract-term interpretation and extended formats are later rules. Only after the baseline works should the trained Self-RAG-inspired adaptation be integrated.

## Subdirectories & System Boundaries

- **`backend/`:** FastAPI, PostgreSQL repository, parser subprocess, private object storage, Celery worker/dispatcher and unit/integration tests. Entry point: [api.py](./backend/documind/api.py).
- **`frontend/`:** The local Next.js workspace, protected API forwarding, source viewer and Playwright scenarios. Entry point [page.tsx](./frontend/app/page.tsx).
- **`migrations/`:** Frozen Alembic schema and PostgreSQL source-integrity triggers; [initial revision](./migrations/versions/0001_native.py).
- **`scripts/`:** [Private local configuration](./scripts/init_local.py) and [live synthetic smoke check](./scripts/smoke.py).
- **`.github/workflows/`:** [CI test workflow](./.github/workflows/tests.yml) for local-service integration tests.
- **`docs/`:** Product and technical specifications, decisions, acceptance plans and status. Start with [product scope](./docs/PRODUCT_SCOPE.md); [INFERENCE.md](./docs/INFERENCE.md) owns the two connection options and model-service boundary. These files do not implement services.
- **`reference/`:** The supplied [original Self-RAG specification](./reference/original_self_rag_spec.pdf), used as research context rather than the current application contract.

`docs/` and `reference/` contain non-code material. The linked source files above are current entry points; component READMEs have not been generated for the newly implemented packages.

### Documentation map

| File | Purpose |
|---|---|
| [`docs/LOCAL_DEVELOPMENT.md`](docs/LOCAL_DEVELOPMENT.md) | Actual local setup, implemented routes, testing and recovery |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Components, interfaces, data flows, runtime patterns |
| [`IMPLEMENTATION.md`](IMPLEMENTATION.md) | Dependency-ordered implementation plan with pass/fail checks |
| [`AGENTS.md`](AGENTS.md) | Permanent agent workflow, conventions and context maintenance |
| [`MEMORY.md`](MEMORY.md) | Durable facts, constraints and lessons |
| [`PROGRESS.md`](PROGRESS.md) | Current implementation, pending work and dated validation |
| [`docs/PRODUCT_SCOPE.md`](docs/PRODUCT_SCOPE.md) | Users, core flows, MVP vs extended scope |
| [`docs/CHANGES_FROM_PDF.md`](docs/CHANGES_FROM_PDF.md) | **Every major intentional departure from original PDF** |
| [`docs/SOURCE_TRACEABILITY.md`](docs/SOURCE_TRACEABILITY.md) | Requirements by original PDF page and their destination |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | PostgreSQL entities, provenance and index schemas |
| [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) | Versioned REST/SSE interface, examples and errors |
| [`docs/INFERENCE.md`](docs/INFERENCE.md) | Connection options, trained runtime API, release identity and Kaggle lifecycle |
| [`docs/SELF_RAG_SPEC.md`](docs/SELF_RAG_SPEC.md) | Token conventions, QLoRA plan, branching engine and policies |
| [`docs/RETRIEVAL.md`](docs/RETRIEVAL.md) | Authoritative retrieval budgets, tokenizers and ranking |
| [`docs/EVIDENCE.md`](docs/EVIDENCE.md) | Claim checks, abstention and first deterministic comparison rule |
| [`docs/INGESTION.md`](docs/INGESTION.md) | Parsing, OCR, tables and provenance |
| [`docs/EVALUATION.md`](docs/EVALUATION.md) | Baselines, datasets, metrics, leakage prevention |
| [`docs/DATASETS.md`](docs/DATASETS.md) | Corpus construction and benchmark rules |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Threat model and privacy controls |
| [`docs/TESTING.md`](docs/TESTING.md) | Required tests and end-to-end acceptance scenarios |
| [`DECISIONS.md`](DECISIONS.md) | ADR style decisions and unresolved questions |
| [`docs/CODEX_BOOTSTRAP_PROMPT.md`](docs/CODEX_BOOTSTRAP_PROMPT.md) | Optional first prompt to give Codex |
| [`.env.example`](.env.example) | Local configuration template; later model/endpoint fields reserved |

## Contract ownership

- DATA_MODEL owns persistent enums, lifecycle and provenance constraints.
- INGESTION owns job recovery and publication of matching indexes.
- RETRIEVAL owns tokenization and retrieval budgets; production is hybrid-v3, while source-512-v1 is an opt-in historical experiment.
- EVIDENCE owns support decisions and calculation rules.
- API_CONTRACT owns HTTP/SSE, idempotency and replay.
- INFERENCE owns connection behavior, capability admission and model-service deployment; SECURITY owns credential and outbound-network safeguards.
- SELF_RAG_SPEC owns the training/inference protocol; EVALUATION owns metrics and split rules.
- IMPLEMENTATION owns dependency gates; root PROGRESS records actual completion, MEMORY holds durable lessons, and AGENTS requires automatic maintenance. Architectural/workflow changes append ADRs in root DECISIONS. Legacy docs status/decision paths are pointers only.

Summaries link to these owners. Once application code exists, executable enums/OpenAPI and contract tests must agree with their owner; generated client types must not redefine them independently.

## Global Side Effects

Local commands start Docker services, apply Alembic migrations and initialize a private workspace/store. Uploads write originals, PostgreSQL jobs and source records; workers render PNGs, and the dispatcher submits Redis/Celery work and removes aged orphans. DELETE revokes access before cleanup. Files and metadata persist in Docker volumes; the token/private configuration remains outside source control. See [recovery and limits](./docs/LOCAL_DEVELOPMENT.md#recovery-and-limits).

## System Verification

```sh
uv sync --frozen --python 3.12
uv run python scripts/init_local.py
docker compose up -d --build
uv run pytest -q
uv run ruff check backend scripts migrations
uv run python scripts/smoke.py
```

Run configuration initialization once. Integration tests use a disposable PostgreSQL database, and the smoke check uses the running app and deletes its synthetic fixture. Native preview runs without model or Kaggle credentials. The [local runbook](./docs/LOCAL_DEVELOPMENT.md) covers private Bearer access and optional Phase 2 setup. [Project status](./PROGRESS.md) records verification and pending gates.

## What not to claim

The original PDF includes **targets and example resume statistics**, not measured DocuMind results. Do not state that faithfulness improved 17 points, 33% of queries skipped retrieval, skipped accuracy was 84%, or latency dropped 40% until a reproducible experiment produces those values. Original GPU quotas and training durations are estimates and can change.

## License / source notice

The source PDF was supplied by the project owner and is packaged for internal project context. Check individual dataset, model, OCR and library licenses before downloading, redistributing or publishing assets.
