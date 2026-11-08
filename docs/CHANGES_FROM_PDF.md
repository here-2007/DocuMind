# Changes from the original PDF

**Comparison basis:** user-supplied 7-page `reference/original_self_rag_spec.pdf` (*Self-RAG Project Spec*, v1.0). The table distinguishes **kept**, **replaced**, and **added** architecture. Changes are explicit design decisions for DocuMind, not hidden claims that the PDF requires them.

## 1. Inherited design and current treatment

| PDF page | Original requirement | DocuMind commitment |
|---|---|---|
| 1-3 | Fine-tune Llama 3.2 3B Instruct with 4-bit QLoRA | Keep as research model target |
| 2-3 | Reflection families `[Retrieve]`, `[IsREL]`, `[IsSUP]`, `[IsUSE]` | Keep categories; define categorical labels / tokenizer mapping before training |
| 2,4 | Qdrant dense + BM25, RRF, cross encoder | Keep ranking stack |
| 2,4 | `all-MiniLM-L6-v2` / `rank_bm25` / `ms-marco-MiniLM-L6-v2` | Replace dense embedder with EmbeddingGemma 2; retain BM25 and cross encoder (D20) |
| 4 | 512 token chunks, 64 token overlap | Retained only as source-512-v1 historical experiment; production uses bounded hybrid-v3 (D17, D20) |
| 4 | RRF `k=60`, rerank top 20, return top 5 | Keep |
| 3-4 | Branch generation on retrieval token, judge relevance/support, cap retrieval calls at 3 | Keep intent, strengthen actual stopping/continuation tests |
| 4-5 | Naive RAG comparison, 200 held-out Q&A, faithfulness/correctness/skip/latency | Keep, add document-specific evaluation tracks |
| 7 | Asai et al. paper anchor | Keep; benchmark reproduction claims require stricter verification |

## 2. Deliberate architectural differences

| # | Topic | PDF states | DocuMind design | Why / consequence |
|---|---|---|---|---|
| D01 | Domain | GATE CS and UPSC GK QA corpus (p. 3) | Business documents: invoices, purchase orders, contracts and generic document QA | User selected DocuMind; requires document pairs, structured fields and grounded evidence |
| D02 | Input modalities | PDFs extracted with PyMuPDF (pp. 4-5) | Native PDF plus scanned PDF/images; later DOCX, CSV, XLSX | Multimodal document intelligence requires OCR, layout, tables, format routing and quality states |
| D03 | UI | Streamlit, Hugging Face Spaces (pp. 3-5) | **Next.js + TypeScript** frontend, FastAPI backend, SSE visualization | Supports upload workflows, page highlights, multi-document investigation; costs significantly more integration time. Streamlit is not the canonical UI. |
| D04 | Storage | Qdrant + BM25; no app database/object store prescribed (pp. 2-5) | PostgreSQL for records; object storage for files; Qdrant and workspace-scoped BM25 for search | Required for provenance, citations, jobs, workspaces, reporting |
| D05 | Job execution | Not specified | Celery + Redis ingestion workers | OCR, rasterization and indexing are long-running and retryable |
| D06 | Retrieval | Hybrid text search (pp. 2-4) | Same hybrid search **plus SQL/structured retrieval** for exact IDs, amounts, dates and joined records | Dense similarity is not a correct arithmetic or exact-match engine |
| D07 | Reasoning | Passage relevance/support/usefulness scoring (pp. 2-4) | Adds record linking, deterministic decimal validation, discrepancy detection and evidence verifier | Cross-document questions require verifiable computation and grounded entity matches |
| D08 | Citations | Source passage citation (pp. 2-4) | Document/page/element/chunk level citations with viewer bounding boxes | User can inspect original scan, table cell or page |
| D09 | Workflow | Single question and side-by-side naive RAG (pp. 3-4) | Workspaces, upload/status, document viewer, chat/trace, investigations, export reports | Complete application rather than one demonstration page |
| D10 | Build order and duration | Training first, 6 weeks (pp. 3-5) | **Baseline ingestion + retrieval first**, independent product/research gates; 16-week estimate is optional context | De-risks project by proving data quality and evidence paths before expensive training; not a PDF requirement |
| D11 | Evaluation | 200 QA + four central metrics (pp. 4-6) | Keep baseline and add OCR/table/field/citation/discrepancy/ranking evaluations and ablations | Must show actual document understanding and compare both model and non-model components |
| D12 | Retrieval skipping | Model decides whether to retrieve (pp. 1-4) | **Policy forces retrieval** for private/document-specific claims; skip decisions tested on eligible general or self-contained queries | Safety and evidence correctness take precedence over skip-rate optimization |
| D13 | Deployment | Streamlit on Hugging Face Spaces; Kaggle T4 training estimate (pp. 4-5) | Docker Compose local development; managed Kaggle or custom inference endpoint (D21) | Source deployment does not cover app stores/jobs or provider lifecycle; platform permission, hosting and capacity remain validation gates |
| D14 | Security | No detailed threat model | Workspace isolation, prompt-injection handling, upload validation, confidentiality policy, audit | Document uploads may contain sensitive personal/business data |
| D15 | Data generation | ~5K GATE/UPSC passage-QA pairs with model critic annotation (p. 3) | Domain specific annotation + labeled no-retrieval/irrelevant/unsupported/cross-doc examples; seed volume **target**, not guaranteed | Original annotations are not directly suitable for DocuMind tasks |
| D16 | Token definition | Four token names mentioned but no complete formal grammar (pp. 2-4) | Freeze project-specific token IDs/label grammar and validate vs paper before training | Prevents a nonfunctional token loop or incorrect claims of exact paper replication |
| D17 | Encoder length | 512-token chunks with MiniLM, no truncation contract | Initially hybrid-v2 used 240/64 MiniLM chunks and 256/512 encoded limits; hybrid-v3 supersedes the embedder and chunk tokenizer while retaining 240/64 and the 512-token reranker cap (D20) | MiniLM defaults truncate beyond 256 word pieces; source-512-v1 is opt-in and records truncation; ADR-011 |
| D18 | Training/inference protocol | Token families and branching sketch | selfrag-v1 adds Continue, Finish and EndSegment, mandatory masks, bounded candidates, critic-v1 scores and independent support checks; serial greedy selection | A declared trained Self-RAG-inspired adaptation, not exact paper beam-search reproduction; ADR-016 |
| D19 | Execution/release contract | Demo streaming and training-first release | Durable query creation plus GET event replay; product ships independently of research | Crash/reconnect semantics and useful baseline releases; ADR-017/018 |
| D20 | Embedding model | all-MiniLM-L6-v2 | User-selected `google/embeddinggemma-2`, text-only, normalized 768-dimensional vectors; hybrid-v3 uses reranker-tokenized 240/64 chunks and an 8192-token dense ceiling | New prompts, tokenizer checks and isolated index identity; reranker and Llama generator retained; no measured quality claim; ADR-020 |
| D21 | Inference deployment | Kaggle training estimate and demo deployment; no provider connection contract | Managed user Kaggle or custom endpoint; ordinary endpoints use baseline, admitted complete trained runtimes use Self-RAG; fully local runtime needs no external key | Fixed release bundle, API serving, scoped credentials and explicit connection changes; no automatic local critic or provider downgrade; ADR-021 |

## 3. Implementation details **proposed**, not specified by original PDF

- Python 3.11+, FastAPI, Pydantic v2, Postgres schema, Alembic migrations.
- Docker Compose for Qdrant, PostgreSQL, Redis and local S3-compatible object storage.
- Separate extraction/index lifecycle, durable leased jobs, parser versioning, request-key deduplication, SHA-256 integrity, immutable publication snapshots, UTC timestamps and canonical geometry schema.
- HTTP query creation and replayable GET SSE, one durable terminal outcome, normalized errors and structured trace IDs.
- Query routing modes `GENERAL`, `DOCUMENT_QA`, `CROSS_DOCUMENT`, `CALCULATION`, `UNSUPPORTED`.
- Decimal arithmetic, extraction confidence handling, human review and report generation.
- Optional additional DocVQA, DocLayNet, DocILE and other task-specific benchmark tracks.

These are engineering choices, not claims that the PDF or Asai et al. specifies them.

## 4. What has **not** been assumed true

- The PDF's claimed 17 point faithfulness improvement, 33% retrieval skips, 84% skip accuracy and 40% speedup appear as **example resume copy / placeholders** (p. 6); they are **not measured project results**.
- The PDF's 30 GPU hours per week, 8-10 hour training run, and CPU-only inference feasibility (pp. 3-5) are **estimates**, not guarantees or current service entitlements.
- The PDF names reflection token categories but not the exact complete paper token schema; paper fidelity is **not assumed**.
- The suggested dataset sizes, licenses and availability need validation. No data, checkpoint, working code, or live application is represented as complete by these documents.

## 5. If a new architectural change happens

Codex must add an entry to `../DECISIONS.md` with context, rejected alternatives and consequences, and extend this file **if the change deviates from the PDF**. Keep README, architecture, interfaces and tests synchronized.

## 6. Audit amendment sources (2026-11-07)

The [MiniLM model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) documents the default 256-word-piece truncation. The [reranker tokenizer](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2/blob/main/tokenizer_config.json) declares a 512-token input limit. Runtime revisions still need pinning and tests; these documents do not establish measured retrieval quality. The [paper §3.3 / Appendix A.3](https://arxiv.org/html/2310.11511v1) describes inference, including evidence reuse and segment scoring; §4 is experiments.

## Native implementation amendment (2026-11-08)

ADR-022 implements the local native slice using PyMuPDF, PostgreSQL/Alembic, leased Celery jobs and a private filesystem object store as the documented local storage equivalent. GPU/training/QA behavior is unchanged and still unavailable. Reprocess/original reads extend source inspection without changing research semantics. See LOCAL_DEVELOPMENT.md for actual implemented behavior and [PROGRESS.md](../PROGRESS.md) for validation.

## Phase 1B implementation refinement

ADR-024 implements the D02 OCR/table extension for PDFs with local PaddleOCR and bounded PyMuPDF/OpenCV ruled-table adapters. Docling/general layout, cross-page table linking and additional upload formats remain deferred. Human review creates new source versions; it does not overwrite the original. These are DocuMind additions, not inherited PDF behavior or measured general accuracy.
