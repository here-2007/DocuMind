# Evaluation and experimental validity

## 1. Source basis

The PDF (pp. 3-6) calls for a 200-question held-out set, naive RAG comparison, RAGAS faithfulness and answer correctness, retrieval skip rate with skip accuracy, latency, 50 retriever spot checks and a 1-page results summary. It states **targets**, not achieved results. DocuMind extends this because OCR, field extraction, cross-document matching and source citations introduce new ways to fail.

**Never fabricate** baseline improvements, model accuracy, skip rates or speedups. The PDF's example numbers (17-point gain, 33% skips, 84% skipped accuracy, 40% faster) are explicitly placeholders (p. 6).

## 2. Research questions

- RQ1: Does the trained reflection-token model reduce unsupported answers versus the same-base naive RAG baseline on document QA?
- RQ2: On **eligible** questions, does trained retrieval control avoid retrieval without significantly harming correctness?
- RQ3: Do BM25 + dense + RRF + reranker improve source retrieval over either component alone?
- RQ4: Can deterministic cross-document validation find grounded amount/date mismatches more reliably than LLM-only comparisons?
- RQ5: How do OCR/table quality and missing evidence affect end-to-end correctness and citation trust?

## 3. Dataset manifest

Build a frozen `evaluation/datasets/manifest.jsonl` with one record per question, e.g.:

```json
{
  "id": "qa_0001",
  "split": "test",
  "document_group": "test_group_a",
  "workspace_fixture": "fixture_1",
  "document_ids": ["<doc_uuid>"],
  "query": "What is invoice INV-100 total?",
  "query_type": "DOCUMENT_QA",
  "retrieval_policy": "required",
  "ground_truth_answer": "INR 1100.00",
  "answerable": true,
  "gold_evidence": [{"document_id":"<doc_uuid>", "page_number":1, "element_id":"<element_uuid>"}],
  "gold_numeric_fields": {"grand_total":"1100.00", "currency":"INR"},
  "source": "synthetic_fixture_v1",
  "reviewed_by_human": true
}
```

Freeze at least 200 unique QA items as a **planned target**, not preexisting fact. Stratify by query type, source quality, answerability and evidence policy. Maintain separate labeled datasets for OCR/layout/field extraction and discrepancy tasks. A Q&A set does not automatically provide ground truth for every task.

## 4. Baselines and controls

Start tiny licensed/synthetic development fixtures in Phase 1A; run retrieval and answerability checks in Phase 2, browser/replay checks in Phase 3 and calculation tests in Phase 4. Freeze grouped held-out research data before Phase 5 annotation/training; Phase 8 consumes it without retuning. Dataset fixture IDs map through a versioned loader to real stored UUIDs/anchors, so fresh database creation does not invalidate the manifest.

Every run records retrieval profile, tokenizer hashes, encoded input limits, truncation counts, publication/manifest hash, evidence-verifier version, supported question types and unresolved failures. Record the complete embedding identity from RETRIEVAL.md, including prompts, modality, dimension and normalization. Default hybrid-v3 must have zero silently truncated inputs. source-512-v1 is a separately labeled truncating historical experiment; never compare its baseline against hybrid-v3 Self-RAG without identifying a retrieval confound. Direct/template-only output and actual model generation are reported separately.


| Label | Setup | Why |
|---|---|---|
| `dense_only` | Same embedder + vector index, no BM25 | Retrieval ablation |
| `bm25_only` | Same chunks + BM25, no dense | Exact-key ablation |
| `hybrid_rrf` | Dense + BM25 + RRF, no cross encoder | Ranking ablation |
| `naive_rag` | Always retrieve top 5 via full hybrid stack, same base LLM/prompt budget | **Primary PDF baseline** |
| `trained_self_rag` | QLoRA reflection checkpoint + actual control/critic loop, same corpus/index | Research target |
| `forced_retrieval_self_rag` | Same trained checkpoint with skip disallowed | Isolate decision policy impact |
| `deterministic_comparator` | Structured records + Python/SQL rules | Cross-document fidelity |
| `llm_only_comparator` | Optional, carefully scoped and logged | Attribution of value to deterministic rules |

Record connection kind/config version, provider/model identity, release/quantization, capability probe, runtime hardware and network/queue/cold-start time separately. Provider-managed aliases without immutable revisions are marked accordingly. Ordinary hosted models are product baselines, not the same-base control: compare trained Self-RAG against pinned Llama baseline on the same corpus/profile for research attribution. Connection failures are reported, never silently replaced by another provider.

Where baselines differ in fine-tuned weights, state so; a gain could be due to adaptation data rather than retrieval control alone. Use comparable compute budget/context size and model temperature/seed where possible, and disclose unavoidable differences.

## 5. Metrics and definitions

### Primary metrics inherited from PDF

- **RAGAS faithfulness**: evaluator-estimated support of answer claims by selected evidence. Save evaluator version, prompt, traces and evaluator settings; it is not an infallible ground truth measure.
- **Answer correctness**: exact match after documented normalization for extractive values; rubric-based judge and human spot checks for free-form questions. Numeric answers use Decimal exactness/tolerance rules.
- **Retrieval skip rate**: `number of queries with zero retrieval calls / number of evaluated queries`. Also calculate **eligible skip rate** on only policy-eligible queries.
- **Skip accuracy**: `correct zero-retrieval answers / zero-retrieval answers`; report denominator and confidence interval. If no skips, report N/A, not 0%.
- **Latency**: end-to-end wall time, mean/median/p95, and separate ingest/retrieve/generate/verify time. Report hardware, batch, warmup, concurrency and whether model loaded before timing.

### Additional DocuMind metrics

- **Retrieval need decision:** score raw model predictions against adjudicated need labels separately from policy-effective actions. Report overrides, Continue/evidence-reuse decisions and retrieval attempts (including failed attempts). Policy-enforced retrieval is not a model accuracy success.
- **Retriever:** Recall@5, Precision@5, MRR, NDCG@5, evidence recall and per-stage latency.
- **Citation:** citation source validity, page accuracy, span accuracy; supported claim rate and unsupported claim rate. Do not count a syntactically valid page link as true support.
- **Extraction:** OCR CER/WER, table cell and structure accuracy, normalized field precision/recall/F1.
- **Cross-document:** record linkage precision/recall, discrepancy precision/recall/F1 by finding type, arithmetic accuracy, abstention accuracy.
- **Robustness:** percentage of unanswerable queries correctly abstained; performance by poor scan, conflicting docs and incomplete metadata.

Report numerator/denominator or sample counts for all percentages. Handle zero denominators explicitly.

## 6. Preventing benchmark contamination

- Split train/validation/test at **document group** level; group related contract/invoice/PO documents and derived synthetic variants.
- Do not tune prompts, score weights, reranker thresholds or model hyperparameters on the frozen test set.
- Do not use test answers in synthetic training/critic label generation.
- Version document corpus snapshots, indexes, extraction/model code and evaluation manifests. Record model source/commit and artifact hashes.
- For deterministic comparisons, hold out different templates, vendors, currencies and mismatch types, not just different query wording.
- If public datasets don't provide linked document pairs, create a **clearly labeled synthetic** controlled benchmark. Never describe synthetic fixtures as naturally occurring real-world evidence.

## 7. Execution protocol

1. Verify pinned publication membership and that all selected documents are searchable in that publication. Freeze evaluation manifest.
2. Run naive RAG baseline with recorded configuration; store **every** answer, sources, decision trace and latency.
3. Run trained Self-RAG (if available) on identical scope and document set; record model version, policy overrides and actual retrieval calls.
4. Run retrieval ablations and discrepancy benchmark independently.
5. Score with saved automatic metrics plus blinded human review of stratified samples; resolve evaluator disagreements.
6. Write `results_raw.jsonl`, `results_summary.csv`, `run_config.json`, `environment.json`, error cases and at least two real example traces.
7. Publish comparison tables and limitations. Any unavailable baseline must be marked not run, never treated as zero.

## 8. Minimum output tables

**Table 1: QA** | variant | N | correctness | faithfulness | citation precision | latency median/p95 |

**Table 2: retrieval policy** | variant | eligible N | mandatory N | overall skip% | eligible skip% | skip accuracy | decision F1 |

**Table 3: retrieval and document tasks** | variant | Recall@5 | MRR | extraction field F1 | discrepancy F1 | compute time |

Only put meaningful, available task columns in each subtable. Use `N/A` for inapplicable metrics. Graphs can show p50/p95 latency and quality tradeoffs with confidence intervals where suitable.

## 9. Reality check on targets

The PDF suggests faithfulness +10-20 points, skip accuracy over 80%, and skipped queries around 40% faster (p. 6). These are **hypotheses/aspirations**, not success criteria guaranteed by architecture. For document-specific queries, correct retrieval often matters more than improving skip rate. A credible project with smaller measured gains and fully inspectable failure cases is preferable to unsupported numbers.
