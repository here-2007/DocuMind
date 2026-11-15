# Phase 8 controlled evaluation

**Historical report.** The November 16 remediation found three distinct evaluator source identities across the broader GPU shards. The combined figures below describe those saved outputs. Consult the remediation evidence for corrected per-shard raw candidate scores and a new retrieval run that honors frozen case scopes.

The frozen 200-case synthetic benchmark measured **172/200 correct baseline answers** and **162/200 correct trained Self-RAG answers**. Both arms used Qwen3-4B-Instruct-2507 at revision `cdbee75f17c01a7cc42f958dc650907174af0554`, NF4 and a Tesla T4. The adapter manifest is `0cfdee37208cb418e41727126882f73fcf1eb8812ee01c03dd19a24f161c2543`. [Artifact-bound results](phase8-benchmark-2026-11-15.json).

## Broader synthetic cohort

The manifest SHA-256 is `6bf099104bb6211a35f5f30497a0f0b4c5c53a8e3e50851f7c76ebe73e443c3b`. It contains 40 invoice groups, 80 PDFs, 120 questions with exact gold evidence, 40 unanswerable or ambiguous questions and 40 eligible general questions. It was created after checkpoint selection and frozen before paired scoring. There were zero transport errors across the three arms.

| Arm | Exact accuracy | Citation precision | Citation recall | Warm median | Warm p95 |
|---|---|---|---|---|---|
| Base naive RAG | 172/200, 86% | 110/110, 100% | 110/120, 91.67% | 1.42 s | 2.52 s |
| Trained integrated Self-RAG | 162/200, 81% | 88/88, 100% | 88/120, 73.33% | 6.18 s | 17.17 s |
| Trained with support gate and rank contribution disabled | 162/200, 81% | 88/88, 100% | 88/120, 73.33% | N/A | N/A |

Self-RAG skipped retrieval in 40/200 cases, including all 40 eligible questions, with **34/40 correct skips**. The baseline always retrieved. Both arms correctly abstained in 40/40 unanswerable cases. The trained arm correctly repeated all 20 supplied references and answered 14/20 additions. Baseline matched 4/20 references and 18/20 additions.

The observed paired accuracy difference was −5 percentage points. The 95% interval from 4,000 bootstrap draws over document groups was [−11, +1.5] points. There were 22 cases where only Self-RAG was correct and 32 where only the baseline was correct. The support-critic ablation changed zero final answers. It retains relevance, utility and the independent evidence verifier.

All 32 answerable document misses in the trained arm abstained. Their traces included 50 relevance rejections and five support rejections across candidate steps. The frozen result retains those misses for future development data design. Future changes require a fresh holdout. [Protocol](../EVALUATION.md#10-phase-8-controlled-comparison).

## Retrieval ablations

These queries search all 80 documents. The paired QA arms use each question's declared source selection. Gold matching requires the correct document, page and complete span.

| Retrieval | Gold span in top 5 | MRR@5 | NDCG@5 | Median retrieve/rank time |
|---|---|---|---|---|
| Dense | 36/120, 30% | 0.2443 | 0.2580 | 0.0372 s |
| BM25 | 68/120, 56.67% | 0.4950 | 0.5127 | 0.0008 s |
| Hybrid RRF | 65/120, 54.17% | 0.4603 | 0.4804 | 0.0378 s |
| Hybrid reranked | 58/120, 48.33% | 0.3471 | 0.3815 | 0.1027 s |

BM25 led on these explicit identifiers and labeled fields. The result measures this synthetic collection and question grammar. All arms share the same chunks and pinned encoding identity. Dense timing includes query encoding. BM25 omits that work. Hybrid variants include it plus their own ranking time.

## Separate document measurements

Three scanned text fixtures had **0/228 character edits** and **0/36 word edits**. A native and scanned ruled table each reproduced all six cells and their row/column structure, yielding **12/12 cells** and **2/2 exact structures**. Twelve native invoice/PO records recovered **72/72 key fields**. Six paired comparison outcomes matched their exact labels and Decimal differences. The discrepancy matrix had two true positives, four true negatives and zero false positives or negatives. These are small controlled fixtures.

## Previously used pre-training cohort

The separate 60-case v4 test cohort retains corpus SHA-256 `0f3864f7040332dbeefd6d5bdd7dc82685bcadf513d74c67617e897f13321beb`. It was frozen before v4 training and used earlier in Phase 6 validation.

| Arm | Exact accuracy | Citation precision | Citation recall | Warm median | Warm p95 |
|---|---|---|---|---|---|
| Base naive RAG | 45/60, 75% | 24/24, 100% | 24/24, 100% | 1.03 s | 2.17 s |
| Trained integrated Self-RAG | 60/60, 100% | 24/24, 100% | 24/24, 100% | 3.21 s | 6.50 s |
| Support gate and rank contribution disabled | 60/60, 100% | 24/24, 100% | 24/24, 100% | N/A | N/A |

Trained retrieval control skipped all 36 eligible cases and answered 36/36 correctly. The baseline always retrieved. There were zero model transport errors. The paired accuracy difference was +25 points, with a grouped bootstrap interval of [+17.86, +32.26] points. These figures apply to the previously used synthetic cohort. The broader result above shows the remaining generalization gap.

## Reproduction and limitations

Use the commands in [LOCAL_DEVELOPMENT.md](../LOCAL_DEVELOPMENT.md#phase-8-frozen-benchmark). Private run directories retain every result, PDF, model step, summary CSV, frozen configuration, hardware profile and source digest. The report reader checks the original PDF text and hashes, rejects duplicate cases and different model profiles, and recomputes recorded scores. Twenty blinded review cases and their separate key are generated from actual outputs.

The broader set is post-training, synthetic and automatically labeled. Blinded human verdicts, RAGAS and representative 200-case pre-training evaluation remain open.

The same 512-token context and 128-token output ceiling apply to both arms. The prompts, adapter weights and number of model calls differ. The base generator uses KV caching, while the current trained decoder recomputes prefixes. Latency includes local retrieval and the private model gateway. Queue, API admission, browser and cold provisioning time are outside query timing. The support ablation reuses identical model prefixes and has no latency comparison. Actual dollar cost and physical GPU deallocation are unavailable.

The full local suite passed **297 tests with zero skips**, including both Chromium workflows, the trained CPU pilot and the populated migration check. A clean native Compose export passed upload, preview, immutable review and deletion and removed its disposable resources. A clean local source clone matched all 175 file hashes and passed the 13 evaluation tests against the existing locked cached environment. Public remote CI remains unverified. Kaggle accepted cancellation for five identified sessions. One additional creation returned no operation or session receipt and remains flagged for reconciliation. Its owned notebook's version status was observed as Error. That observation cannot establish allocation cleanup. The original failed upload and harness attempts remain in the private evidence directory.
