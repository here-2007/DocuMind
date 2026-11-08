# Suggested first Codex instruction

Phase 0/1A is now implemented locally. This initial prompt is retained as its scope contract; use [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md) and [PROGRESS.md](../PROGRESS.md) for actual commands/results and begin the next milestone only when requested.

Copy the following prompt into a Codex task **after providing this entire folder/repository**. It is intentionally limited to a feasible first milestone rather than demanding 16 weeks of software in one shot.

---

You are the implementation engineer for DocuMind AI. Treat the files in this repository as the current product and technical requirements. Read root `AGENTS.md`, `MEMORY.md`, `PROGRESS.md`, relevant ADRs in `DECISIONS.md`, then `README.md`, `docs/CHANGES_FROM_PDF.md`, `docs/PRODUCT_SCOPE.md`, `ARCHITECTURE.md` and `IMPLEMENTATION.md` first. Consult specific contracts for the code you touch. The source PDF in `reference/original_self_rag_spec.pdf` is the research baseline, not a complete application spec.

**Task:** Implement Phase 0 and Phase 1A native-PDF extraction/preview. Deliver a working FastAPI backend, Docker Compose for PostgreSQL, Qdrant, Redis and a private local object store (or a documented equivalent), SQLAlchemy/Alembic migrations, a real document upload endpoint, a Celery ingestion worker that extracts native text and geometry with PyMuPDF, document/job/page/element read endpoints, and integration tests. Use durable DB jobs/request keys, lease-safe retries and immutable scoped source records. Extraction completion enables preview while index_status remains not_started; do not require indexing for this slice. Bootstrap the Next.js frontend shell only if it does not delay the backend slice.

**Constraints:**
- Keep inference and managed Kaggle disabled for Phase 0/1A. No API credentials, model downloads, provisioning or empty connection modules are required; INFERENCE.md is for later milestones.
- Do not implement fake Self-RAG logic or pretend a QLoRA checkpoint exists.
- Do not make up model accuracy, latency, training runtime or dataset access.
- Enforce workspace scoping, retain page/element source anchors, persist originals safely and handle invalid/empty files.
- Use the exact API/data model contracts where applicable; document deliberate adjustments as ADRs.
- Don't generate dozens of empty placeholder modules. Prioritize functionality that can be run and tested.
- Use `.env.example`; never commit secrets, model weights or sensitive upload files.

**Required output at end of task:**
1. Summary of files created/changed.
2. Exact local setup commands.
3. Test commands and actual pass/fail results.
4. Known blockers, unimplemented items and precise next task.
5. Any changes to root `DECISIONS.md` / `docs/CHANGES_FROM_PDF.md`.

Stop at the Phase 1A vertical slice unless the user explicitly asks you to continue to the next milestone.

---

## Subsequent Codex task pattern

When Phase 1A passes, request: *"Implement Phase 2 hybrid retrieval exactly as `docs/RETRIEVAL.md`, including hybrid-v3 token budgets, pinned publication/workspace filters, matching Qdrant/BM25 snapshots, RRF k=60, cross encoder top-20 -> top-5, evidence-v1 abstention, one tested custom-endpoint baseline adapter under `docs/INFERENCE.md`, owner-scoped secret handling, explicit failures and standalone retrieval tests. Do not start QLoRA training."*

After that, move to the UI baseline and cross-document deterministic comparison, and then to reflection data/fine-tuning. Phase 6 adds the full trained runtime API; gated Phase 6K adds managed Kaggle. Neither blocks the baseline or comparison product. Every milestone should end with running code and tests, not a claim that files were generated.
