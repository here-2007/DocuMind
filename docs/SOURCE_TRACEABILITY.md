# Source PDF to implementation traceability

The source is `reference/original_self_rag_spec.pdf`, 7 pages. Page references are **PDF page numbers**, not arbitrary section IDs. 'Derived' indicates a DocuMind engineering extension; it does not appear in the source.

| Source page/section | Requirement actually stated | DocuMind target | Document / implementation area |
|---|---|---|---|
| p. 1 / Problem | Avoid always-on retrieval; learn retrieval decisions | Reflection model and policy-aware skips | `docs/SELF_RAG_SPEC.md`, `query_router` |
| pp. 1-2 / Architecture | Llama-3.2-3B + QLoRA; retriever; relevance/support/usefulness | Qwen3-4B replaces the base under D21. The training and retrieval method is retained | `ARCHITECTURE.md` |
| p. 2 / Fine tuning | 4-bit bitsandbytes and `[Retrieve]`, `[IsREL]`, `[IsSUP]`, `[IsUSE]`; LLM-critic labels | Versioned labels and trained checkpoint | `docs/SELF_RAG_SPEC.md` |
| p. 2 / Retrieval | Qdrant, all-MiniLM-L6-v2, BM25, RRF, cross encoder | Ranking retained; embedder replaced by EmbeddingGemma 2 (D20 / ADR-020) | `docs/RETRIEVAL.md` |
| p. 3 / Inference | Intercept token; retrieve, inject, continue; score candidates | Adapted with explicit selfrag-v1 state/mask/scoring contract (D18) | `docs/SELF_RAG_SPEC.md` |
| p. 3 / Corpus | GATE CS and UPSC | **Replaced** with business documents | `docs/CHANGES_FROM_PDF.md`, `docs/DATASETS.md` |
| p. 3 / UI | Streamlit reflection trace | **Replaced** with Next.js + SSE | `docs/API_CONTRACT.md`, `docs/CHANGES_FROM_PDF.md` |
| p. 3 / Training | ~5K annotated pairs, r=16, 3 epochs; manual 20 tests | Research starting targets with honest provenance | `IMPLEMENTATION.md`, `docs/SELF_RAG_SPEC.md` |
| p. 4 / Retrieval | 512 tokens / 64 overlap; RRF k=60; top 20 rerank, top 5 return; 50 manual queries | Ranking retained; chunk budget amended by D17 / ADR-011 and tokenizer contract by ADR-020 | `docs/RETRIEVAL.md` |
| p. 4 / Inference | Max 3 retrieval attempts | Hard cap with tests | `docs/SELF_RAG_SPEC.md` |
| p. 4 / UI | Trace, citation, compare mode | Retained user experience with changed stack | `docs/API_CONTRACT.md` |
| pp. 4-5 / Evaluation | 200 held-out QA; faithfulness, correctness, skip, latency | Retained + specialized document tasks | `docs/EVALUATION.md` |
| p. 5 / Full Stack | PyMuPDF, transformers, PEFT, bitsandbytes, RAGAS | Retained where applicable | `README.md`, `docs/INGESTION.md` |
| p. 6 / Targets | +10-20 faithfulness pts, >80% skip accuracy, ~40% latency | **Aspirations only**, no actual measurements | `docs/EVALUATION.md` |
| p. 6 / Resume | 17pt/33%/84% example text, marked placeholder | Excluded from claims until measured | `README.md` |
| p. 7 / Paper | Asai et al. 2024, arXiv 2310.11511 | Paper anchor; rigorous fidelity check | `docs/SELF_RAG_SPEC.md` |
| No equivalent | OCR, layout, table extraction, document fields | **Derived DocuMind addition** | `docs/INGESTION.md` |
| No equivalent | PostgreSQL, object store, workers, workspace permissions | **Derived DocuMind addition** | `docs/DATA_MODEL.md`, `docs/SECURITY.md` |
| No equivalent | Document comparison, Decimal arithmetic, report | **Derived DocuMind addition** | `docs/PRODUCT_SCOPE.md`, `docs/API_CONTRACT.md` |
| No equivalent | Managed user Kaggle, custom endpoints, fixed release API and explicit fallback | **Derived DocuMind addition** (D21 / ADR-021) | `docs/INFERENCE.md`, `docs/SECURITY.md` |

## How to use this table

Before implementing an algorithm because 'the PDF said so', find the exact source row. If no row supports it, label it as a DocuMind decision and update `../DECISIONS.md`. This is especially important for unvalidated claims about token semantics, benchmarks, timings and GPU entitlements.
