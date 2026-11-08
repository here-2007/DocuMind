# DocuMind AI

**Self-RAG Powered Multimodal Document Intelligence and Cross-Document Reasoning Platform**

**Status:** Phase 0/1A review fixes and bounded Phase 1B PDF extraction are implemented locally: native text, optional English PaddleOCR, ruled-table cells and immutable human review. See [current verification](PROGRESS.md) and [runtime setup](docs/LOCAL_DEVELOPMENT.md). Search, generation, Next.js and the broader research platform remain planned. General scan accuracy, public deployment and production capacity are unverified.

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

This root blueprint covers a working **local PDF extraction and preview backend** plus the planned product. Start with [local setup and implemented routes](./docs/LOCAL_DEVELOPMENT.md). OCR is opt-in; search, generation, Next.js and managed Kaggle are later milestones; [project status](./PROGRESS.md) records observed validation.

A workspace where a user uploads business documents (including scanned PDFs), asks evidence-based questions across them, identifies inconsistent facts or amounts, and receives an answer with document/page/element citations and an auditable retrieval trace.

**Signature demo:** upload a purchase order, invoice and contract; ask *“Which invoiced amounts differ from the purchase order and what evidence explains each discrepancy?”* The system parses the documents, matches records, retrieves evidence, validates arithmetic with code, and shows the exact cited pages. It should abstain when documents or extraction confidence are insufficient.

## Global Invariants

The native slice enforces scoped database references, local Bearer access, immutable promoted sources, bounded PDF parsing and fenced job promotion in [backend/documind](./backend/documind/api.py) and the [first migration](./migrations/versions/0001_native.py). The following broader product requirements come from [AGENTS.md — Nonnegotiable rules](./AGENTS.md#nonnegotiable-rules); later retrieval/model requirements remain unimplemented:

- **Evidence over fluency.** Never fabricate a citation, calculation, OCR result or metric.
- **Deterministic work stays deterministic.** Python/SQL handle sums, mismatches and currency rules; the LLM explains findings.
- **Uploaded document questions require retrieval.** Self-RAG can skip retrieval only where doing so does not sacrifice private, changing or document-specific evidence.
- **Preserve source provenance.** Every searchable chunk can be traced to its original document and page; every supported claim traces to one or more chunks/elements.
- **Research claims require evidence.** 'Self-RAG' is a trained reflection-token decision process, not a prompt that prints tags.
- **Ship in vertical slices.** A functioning baseline document QA system must exist before attempting expensive QLoRA training.

## Target stack

| Area | Technology | Role |
|---|---|---|
| Web | Next.js, React, TypeScript, Tailwind | Document viewer, chat, investigation workspace, trace |
| API | FastAPI, Pydantic | Document and question workflows, streaming events |
| Ingestion | PyMuPDF, Docling, PaddleOCR, OpenCV (as needed) | Native text, structure, OCR, tables |
| Storage | PostgreSQL, object storage, Qdrant | Structured metadata, originals, vectors |
| Jobs | Redis and Celery | Asynchronous parsing and indexing |
| Search | `google/embeddinggemma-2`, `rank_bm25`, RRF, `cross-encoder/ms-marco-MiniLM-L6-v2` | Dense + sparse + reranking |
| Generation | Llama 3.2 3B Instruct, Transformers, PEFT, bitsandbytes, TRL | Baseline and trained reflection-token inference |
| Evaluation | pytest, RAGAS, custom scoring, pandas | Reproducible comparison and audit |
| Deployment | Docker Compose first | Repeatable local development; optional hosted deployment later |

This stack is a **target**, not a claim that every dependency works on every CPU/GPU. See `DECISIONS.md`.

### Selected model roles

| Role | Selected model / implementation |
|---|---|
| Embedding | `google/embeddinggemma-2`, text-only, 768 dimensions; hybrid-v3 in RETRIEVAL.md |
| Reranking | `cross-encoder/ms-marco-MiniLM-L6-v2` |
| Baseline generation | Default controlled model: `meta-llama/Llama-3.2-3B-Instruct`; supported external endpoint models allowed with recorded identity |
| Self-RAG generation and critic | Same Llama base with a separately trained reflection-token adapter; no separate serving critic model |
| OCR / tables | CPU PaddleOCR 3.7.0 / PaddlePaddle 3.3.1 with PP-OCRv5 mobile detection + English recognition; PyMuPDF/native and OpenCV/scanned ruled tables. Docling/general layout remains deferred |
| Claim/citation verification | Deterministic evidence-v1 checks; no additional model |

These are fixed model selections, not validated checkpoint revisions. Pin immutable model/tokenizer revisions and compatible dependencies during the relevant implementation milestone. External annotation teachers remain optional and separately configured; no provider is enabled by this selection. ADR-020 records the embedding change; ADR-021 permits explicitly selected external baseline generators and defines the two inference connections.

### Inference connection options

1. **Managed Kaggle:** the user authorizes their Kaggle account; DocuMind provisions and monitors a private notebook running our complete trained model service. This remains unavailable until platform, security and runtime gates pass.
2. **Custom endpoint:** the user supplies an inference endpoint, model ID and credential. Ordinary models provide baseline RAG; only a validated compatible trained runtime provides Self-RAG. No additional local Llama is required for ordinary endpoint generation.

The same connection contract supports a fully local installation and a local trained service without an external provider key. Hosted embedding/reranking stay on DocuMind; fully local deployments keep them on the user's system. Kaggle quota exhaustion never silently switches providers or modes: the user selects a ready endpoint for a new run. Full contract: [inference and deployment](docs/INFERENCE.md), ADR-021.

## Interface

- **Implemented interface:** [API routes](./docs/LOCAL_DEVELOPMENT.md#implemented-routes) provide health/readiness, local workspace access, upload/job/retry/reprocess, original/page/element/image reads and safe deletion. [Repository](./backend/documind/repository.py), [parser](./backend/documind/parser.py), [worker](./backend/documind/worker.py) and [schema](./backend/documind/schema.py) implement the slice. Remaining design APIs are future contracts.
- **Configuration template:** [`.env.example`](./.env.example) declares proposed flags including `INDEXING_ENABLED=false`, `GENERATION_ENABLED=false`, `SELF_RAG_ENABLED=false` and `MANAGED_KAGGLE_ENABLED=false`. `GENERATION_ENDPOINT_URL` and `GENERATION_API_KEY` are blank local-only connection seeds; hosted user credentials belong in the scoped secret-management design in [SECURITY.md](./docs/SECURITY.md). The [settings loader](./backend/documind/config.py) consumes native-slice settings and rejects enabling unimplemented capabilities; future model/endpoint fields remain reserved.
- **Developer instructions:** [AGENTS.md](./AGENTS.md) defines implementation requirements and the required reading order. The documentation index below provides the current human/agent navigation surface.

## The first demo that counts as working

Upload one native-text PDF, finish an ingestion job, ask a question, get an answer grounded in retrieved passages with a clickable page citation and a visible retrieval trace. Then support two documents and the exact invoice/PO grand-total comparison. Both product releases are independent of training. Contract-term interpretation and extended formats are later rules. Only after the baseline works should the trained Self-RAG-inspired adaptation be integrated.

## Subdirectories & System Boundaries

- **`backend/`:** FastAPI, PostgreSQL repository, parser subprocess, private object storage, Celery worker/dispatcher and unit/integration tests. Entry point: [api.py](./backend/documind/api.py).
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

Run configuration initialization once; it refuses to overwrite an existing `.env`. Integration tests use a disposable PostgreSQL database; the smoke check uses the running app and deletes its synthetic fixture. [Local development](./docs/LOCAL_DEVELOPMENT.md) documents alternatives and private Bearer access. [Project status](./PROGRESS.md) records actual test results and pending gates. The next milestone after this native slice is Phase 2 retrieval and baseline generation; no model or Kaggle credentials are required for native preview.

## What not to claim

The original PDF includes **targets and example resume statistics**, not measured DocuMind results. Do not state that faithfulness improved 17 points, 33% of queries skipped retrieval, skipped accuracy was 84%, or latency dropped 40% until a reproducible experiment produces those values. Original GPU quotas and training durations are estimates and can change.

## License / source notice

The source PDF was supplied by the project owner and is packaged for internal project context. Check individual dataset, model, OCR and library licenses before downloading, redistributing or publishing assets.
