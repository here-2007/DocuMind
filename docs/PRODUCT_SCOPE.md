# Product scope and non-goals

## Product statement

DocuMind AI is a **multimodal document intelligence and cross-document reasoning system**. It ingests original files, maps extracted content to inspectable source locations, searches with hybrid retrieval, answers grounded questions with citations, and supports structured discrepancy investigations. A genuine QLoRA-trained Self-RAG reflection loop is its differentiated research capability.

The application runs locally on the user's device. Public application hosting, SaaS accounts and Hugging Face Spaces deployment are outside the current scope.

## Intended users

- A student/engineer running a technically credible AI engineering demonstration.
- An analyst comparing sample invoices, purchase orders and contracts.
- A researcher benchmarking document question answering and retrieval decision making.

Not intended for autonomous accounting authorization, legal adjudication or unattended high-stakes payment decisions.

## Core user stories

1. **Upload** a PDF and see extraction progress, then preview pages; see search availability separately after indexing. Lifecycle fields are defined in DATA_MODEL.md.
2. **Ask** a document-specific question; get an answer and clickable citation to exact source page.
3. **Compare** two or three related documents and get mismatch findings backed by source records and deterministic math.
4. **Inspect a trace** of retrieval and evidence decisions (including real Self-RAG reflection tokens if trained path is active).
5. **Evaluate** identical questions against naive RAG vs Self-RAG and view measured results.
6. **Export** a reproducible investigation report with sources and calculation methodology.

## Inference setup

Configure the fine-tuned reflection runtime on the user's device or in their private Kaggle GPU session. Configure answer generation independently on the device, through private Kaggle, or through a supplied endpoint/model/API key. The local CLI manages private Kaggle sessions and bridges inference to loopback. The UI selects the generator and answer mode.

The trained model chooses retrieval and judges the selected generator's exact answer candidate. The local application enforces workspace policy, critic ranking, arithmetic and citation verification. The integrated trained generator remains a compatible option. Connection failure requires explicit selection for a new run. [INFERENCE.md](INFERENCE.md) owns the model contracts.

Connection setup shows the evidence destination, model identity and availability. Credentials and lifecycle state stay on the user's device. Stop a private Kaggle bridge with its local command and revoke generation credentials in the workspace. Preserve workspace isolation for source integrity. Native preview and deterministic comparisons remain independent of model availability.

## Functional scope by release

| Capability | MVP 1 | MVP 2 | Research release | Extended |
|---|---|---|---|---|
| Native text PDF ingestion | Required | Required | Required | Required |
| PDF source page citations | Required | Required | Required | Required |
| Hybrid Qdrant+BM25+RRF+cross encoder | Required | Required | Required | Required |
| Naive RAG baseline | Required | Required | Required | Required |
| Custom endpoint configuration and tested baseline adapter | Required | Required | Required | Required |
| Compatible trained endpoint | No | No | Required | Required |
| Private Kaggle model bridge | Optional model execution | Optional model execution | Phase 6K lifecycle and model checks | Local operator workflow |
| Next.js upload/chat/viewer | Required | Required | Required | Required |
| OCR scanned PDFs/images | Optional until stable | Required | Required | Required |
| Exact invoice/PO identifiers and grand totals | No | Required | Required | Required |
| Contract rules, amendments and fuzzy linkage | No | Later rules | Optional | Required |
| Deterministic discrepancy validation | No | Required | Required | Required |
| Reflection-token QLoRA checkpoint | No | No | Required | Required |
| Token-level Self-RAG branch loop | No | No | Required | Required |
| Side-by-side evaluation/traces | Minimal baseline trace | Optional | Required | Required |
| Source-cited CSV reports | No | Required | Required | Required |
| Formatted PDF reports | No | Later | Required | Required |
| DOCX/CSV/XLSX ingestion | No | Optional | Optional | Required |
| Application hosting | Local only | Local only | Local only | Local only |

MVP 1 is Phase 3; MVP 2 is Phase 7 (including Phase 1B for scans/tables). Neither requires a trained adapter. General free-form document QA is a staged capability: evidence-v1 initially verifies direct fields and deterministic calculations, and abstains or shows source previews for unsupported question forms. Contract pages can be inspected before automated contract interpretation exists.

## Query categories

| Class | Example | Evidence policy |
|---|---|---|
| General | "Explain what a purchase order is" | Retrieval may be skipped if no source claim requested |
| Document QA | "What is the total on invoice A?" | **Mandatory scoped retrieval** |
| Cross-document | "Which invoice differs from its PO?" | **Mandatory retrieval and record linking** |
| Calculation | "What is the net difference after tax?" | **Mandatory retrieval + deterministic validator** |
| Unsupported | "Approve this payment" | Must not take action; explain limits |

## Explicit non-goals

- Autonomous transfer of money or contract execution.
- A universal OCR engine that guarantees every scan, handwriting and language.
- A substitute for legal, accounting or compliance review.
- Training a new foundational LLM from scratch.
- Perfect fully automatic invoice-to-contract relationship inference without explicit IDs.
- Production readiness merely because Docker containers start.
- Arbitrary website crawling or RAG over the public internet in MVP.
- Claiming official reproducibility of the Self-RAG paper without implementing and validating its exact algorithm.

## Signature demo acceptance scenario

User uploads `purchase_order.pdf` (PO-100, INR 1000) and `invoice.pdf` (PO-100, INR 1100). Both pages and extracted amounts exist as typed records with citations, the same explicit vendor ID, and explicitly matching grand-total basis. The normative calculation and abstention rules are grand_total_match_v1 in EVIDENCE.md. A comparison produces `AMOUNT_MISMATCH` with `difference=100.00`, currency `INR`, matching key `PO-100`, references to both source pages, and an explanation that quotes neither nonexistent values nor unsupported contract terms. If either amount is low-confidence OCR, the result explicitly says `needs_review` rather than confidently accusing a mismatch.

## Success vs failure

**Success:** reproducible end-to-end demo, accurate source navigation, measurable retrieval/evidence quality, robust handling of missing evidence, transparent research evaluation.

**Failure:** attractive UI with no real indexing, unresolvable citations, hallucinated calculations, hard-coded answers, mock reflection tokens, or benchmark claims without saved predictions.
