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

Record both generator and fine-tuned reflection identities, connection/version, execution strategy, inference profile, capability probe, actual hardware and startup/queue/network times. Mark provider aliases without immutable revisions. Evaluate split pairings separately. Controlled research compares integrated trained Qwen with the same pinned base and retrieval profile. Report connection failures explicitly.

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

The operator tool `scripts/evaluate_selected_workflow.py` now measures the selected experimental adapter through real PDF APIs, native ingestion/review, publication, hybrid retrieval, durable query processing, private GPU model steps and independent citation verification. It uses the same disposable stores as integration tests and saves original PDFs, contexts, model outputs and trace identities privately. Production release admission and private-gateway transport are explicitly injected in this isolated harness; the actual pilot capability and loaded weights are preserved. This does not validate production admission, hosted account handling or the browser connection workflow.

The v3 plan freezes 36 test cases plus ten edge/general cases. The v4 plan freezes 60 test cases plus thirteen application regression/long-document cases, for 73 total. Freeze the plan before inference. Report errors and safe abstentions by category. Check final false verified claims, scope violations, citation resolution and retrieval calls independently of answer correctness. Preserve model/corpus/case/PDF hashes and environment with the raw result. Source PDFs remain available offline after the disposable DB is dropped; citation resolution is measured during execution.

The [2026-11-09 run](validation/selected-pdf-workflow-2026-11-09.json) measured 43/46 expected outcomes, zero false verified answers and 26 resolvable scoped citations. Document questions retrieved exactly once, and eligible queries made zero searches. The long tail-field case lacked its distant invoice ID in the model window and was rejected by relevance. Both arithmetic cases ended with an initial Finish decision. These are retained coverage failures, not transport failures. No weights or inference policy were tuned after seeing these outcomes. Use fresh grouped development/holdout data for context/identity and eligible-task improvements before another release claim. This is a synthetic application diagnostic, not a representative benchmark or human approval.

For the follow-up, report raw first actions, policy overrides and final exact answers separately. A correct answer after an `answerable_addition` override demonstrates application recovery. A raw `[NoRetrieve]` followed by the correct number demonstrates the desired trained trajectory. The original arithmetic pair and long PDF remain regressions. V4 adds fresh arithmetic pairs and long-invoice cases for evaluation. [Current validation](../PROGRESS.md#follow-up-on-s01s03--2026-11-09).

The [November 15 evidence](validation/phase6-validation-2026-11-15.json) records 73/73 expected integrated outcomes and 61/73 in the first complete split run. A complete-value instruction, checked on five separate development examples, recovered currency-bearing amounts. Its same-case regression reached 72/73 with no false verified answer; this is not a new unseen holdout. The remaining quoted-instruction case safely abstained after the external generator emitted a reserved reflection string. Weights, verifier and serializer stayed fixed.

Test greedy decoding passed 144/144 episodes; separately collected conditional scores passed 324/324 label checks. Keep their meanings distinct: a counterfactual gold answer and the actually generated answer can have different prefixes despite sharing passage text. Numeric HTTP equivalence uses the exact recorded output prefix and context. The 20-case v4 adjudication is explicitly delegated agent review; it does not count as human evaluation, and the earlier human v3 verdicts are not transferred. Same-base research comparison, representative performance and broader adversarial evaluation remain Phase 8 work.

The PDF suggests faithfulness +10-20 points, skip accuracy over 80%, and skipped queries around 40% faster (p. 6). These are **hypotheses/aspirations**, not success criteria guaranteed by architecture. For document-specific queries, correct retrieval often matters more than improving skip rate. A credible project with smaller measured gains and fully inspectable failure cases is preferable to unsupported numbers.

## 10. Phase 8 controlled comparison

[Phase 8 v1](../evaluation/datasets/phase8-v1.jsonl) freezes 200 synthetic cases in 40 groups, with 80 original PDFs generated from the saved content. The 160 document cases include 120 labeled source spans and 40 ambiguous or missing fields. Forty cases exercise self-contained addition and reference repetition. Cross-document scope asks for a named invoice among two sources. It does not require free-form synthesis. This set was authored after checkpoint selection and frozen before paired scoring. The existing 60-case v4 pre-training test split is a separate, previously used cohort. Keep its results separate from the post-training set.

`evaluate_phase8.py` uses disposable PostgreSQL, Qdrant and object stores with the real parser, publisher, hybrid-v3 retriever, citation verifier, tokenizer and reflection loop. Each arm runs on the same pinned Qwen revision and NF4 profile inside one private Kaggle session for each shard. The naive arm always retrieves top five, packs ranked whole excerpts into the shared 512-token input/output window, and invokes the base once. The trained integrated arm loads the checked v4 adapter and uses the same retriever with its reflection grammar and per-candidate context packing. Both have a 128-token total output ceiling, temperature zero and concurrency one. Apply the same exact field and arithmetic checks after generation. The production baseline endpoint also has a deterministic field fallback. The research arm requires actual generated text. Report prompt, adapter, caching and call-count differences when interpreting results. The research harness does not exercise production query admission or browser/queue latency.

The support-critic ablation removes its hard filter and contribution to rank. Relevance, utility and independent verification stay active. Deterministic steps can be reused only for identical serialized state, context and budget. Divergent prefixes call the real model. This tests the effect of *using* the support critic and has no latency comparison. All four retrieval ablations honor each frozen case’s declared document selection on the same publication. BM25 keeps the publication’s corpus statistics and ranks only eligible documents. Correct document, page and span define relevance. The original workspace-wide scores are historical because some questions need their document selection to identify the answer.

Run schema `phase8-run-v2` declares either `paired` or `retrieval_only`. Every paired case records all three arm outcomes, including explicit failures. Merge only matching run kinds, frozen source hashes and control settings. Each run retains the measured implementation under `source/` and binds it to a trusted source checkout. Paired runs require a hashed environment record with the paired base profile and trained capability. Saved reflection steps must match the shard’s runtime instance and adapter manifest.

Raw candidate accuracy scores the **first nonempty generated text**, after trimming surrounding whitespace, against the exact gold value or quote. The report retains every model attempt with the filter or verification decision where available, and the committed reflection text separately. Rejected text still counts toward the raw score. A row without generated text cannot earn raw credit. Final answer accuracy and citation scoring use the checked application result.

Count transport errors as incorrect and list them separately. Measure successful warm queries from retrieval through answer verification, including the private gateway, before the secondary API check that resolves the citation. Answer accuracy uses exact recorded values and checked abstention. Citation precision divides gold-matching citations by emitted citations. Recall divides those matches by gold-citation cases. Matching requires source, page, exact excerpt and resolved element offsets. The result is a deterministic check of extractive evidence. RAGAS and blinded human review are independent gates. The grouped bootstrap resamples document groups for paired accuracy intervals. Separate synthetic documents measure OCR edit errors, table structure, field exactness and amount discrepancy. [Reproduction commands](LOCAL_DEVELOPMENT.md#phase-8-frozen-benchmark).

Record compute use from the retained session lifecycle and successful per-arm timings. Session allocation has a configured 1,200-second limit, including startup. It does not establish billed GPU time. The run has no billing invoice or measured dollar cost. The [model release record](validation/local-release-2026-11-15.json) records Apache-2.0 for the pinned Qwen base. Weights require the exact matching adapter and CUDA NF4 dependencies. Retrieval weights retain their own registry terms. Installed PyMuPDF metadata identifies AGPLv3 or Artifex commercial licensing. The synthetic document content is project-authored. Reproduction relies on already provisioned retrieval/OCR files and configured Kaggle access.
