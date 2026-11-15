# Dataset and annotation policy

**Source change:** original PDF pp. 3-5 proposes GATE CS / UPSC QA (~5K passage/question training pairs, 200 test questions). DocuMind replaces that application domain with business document QA, document parsing and cross-document comparisons. Do not silently import GATE/UPSC evaluation numbers.

## Candidate sources (access and license must be checked)

| Candidate | Potential use | Critical caveat |
|---|---|---|
| [DocVQA dataset](https://site.docvqa.org/datasets/docvqa) | Question answering over document images | Does not automatically supply invoice/PO linked discrepancies |
| [DocLayNet](https://github.com/DS4SD/DocLayNet) | Layout/reading order benchmark | Layout labels are not numerical cross-document comparison labels |
| [CORD](https://github.com/clovaai/cord) | Receipt field/table extraction | Receipt language and schema may differ from contracts/invoices |
| [DocILE](https://github.com/ibm/DocILE) | Invoice layout and key information extraction | Verify annotation semantics, access and licensing |
| [Patram-Bench](https://patrambench.github.io/) | Potential multilingual/Indian document evaluation | Verify available dataset tasks, redistribution rights and current download path |
| Project-created controlled fixtures | Invoice/PO/contract matching and calculation tests | Must be labeled **synthetic**, with generation seed and immutable gold truths |

Names above are **candidates** rather than a guarantee that every dataset is currently available or suitable. Before using any: confirm license, permitted use, document sensitivity, split structure, annotation format, version and hardware/storage cost. Do not embed or redistribute someone else's data by default.

## Data tracks

1. **Native/scan QA track:** grounded question -> answer and gold page/span. Target >=200 held-out items as in PDF. Include answerable and unanswerable queries.
2. **Retrieval control track:** question -> justified retrieve/skip label and eligible/required policy. Include self-contained general questions and private-doc factual questions.
3. **Parsing track:** page images plus layout/table/OCR labels, scoped to individual parsing metrics.
4. **Structured extraction track:** invoice/PO/contract field values, types, confidence and source spans.
5. **Cross-document track:** linked document bundles + gold record matches, missing links, discrepancies and numeric calculations. This usually requires deliberate annotation or controlled synthetic examples.

## Synthetic bundle example

Bundle `case_0001`: PO `PO-100` line items totalling INR 1000, invoice refers to `PO-100` with amount INR 1100, explicit matching vendor IDs and tax-inclusive grand-total basis on both documents, plus an optional contract for later rule versions. Gold annotations include links, source spans, `AMOUNT_MISMATCH=100.00 INR`, business rule and expected verification/abstention behavior. Include negative bundles where values agree, line item sums disagree, currencies differ and ambiguous references block a conclusion.

Version the fixture generation code, seed, files, gold JSON, validation checks and split group. Avoid using one template with only numbers changed across train and test.

## Training annotation rules

- Bulk annotation is blocked until selfrag-v1 serializer, legal transitions, loss masks and tiny train/save/reload probes pass. Store protocol/serializer hashes with every batch; a protocol change invalidates incompatible annotations.
- Build ~5K curated question/passage pairs as an initial **research target**, subject to actual quality and compute.
- Teacher critic generates tentative reflection labels; verify a stratified subset manually and adjudicate disagreements.
- Label each passage's relevance, answer support, retrieval need and source provenance; don't infer 'fully supported' from answer correctness alone.
- Never send sensitive uploaded business files to external annotation APIs without consent and appropriate privacy controls.
- Partition by document group before synthetic question generation to reduce leakage.
- Record `source`, `license`, `version`, `split`, `generation_method`, `review_status`, and any annotation model and prompt revisions.

## Source manifest (proposed)

```json
{
  "dataset_name": "documind_synthetic_linked_v1",
  "source_type": "synthetic",
  "license": "project-owned",
  "version": "v1",
  "generation_seed": 2026,
  "documents_sha256": ["<hash>"],
  "gold_annotations_sha256": "<hash>",
  "split_strategy": "grouped-by-template-and-case-family",
  "pii_review": "passed",
  "approval_status": "internal"
}
```

This JSON is an example contract. Phase 5 also includes a deterministic generator for a frozen pilot with 12 training, 6 validation and 24 test samples. The 24 test samples comprise 12 retrieval-needed and 12 safe-to-skip cases. Each document case supplies an exact passage, a near-ID negative, partial support, conflicting evidence and an absent-record passage. The manifest checks exact source spans, split assignments and file hashes. The pilot carries synthetic gold labels and no human-adjudication flag. [Runbook](LOCAL_DEVELOPMENT.md#phase-5-offline-reflection-training).

The independent synthetic-v3 diagnostic contains 96 training, 24 validation and 36 held-out samples. Paired records change the visible amount, currency and date. UUID anchors conceal fixture variants. Correct and corrupted candidates share visible source text, requiring support labels to depend on the emitted answer. Three training layouts differ from the validation and test layouts. Freeze [its manifest](../backend/tests/fixtures/phase6_reflection_corpus_v3_manifest.json) before training. These controlled fixtures cover three field families and supplied-reference repetition; they are a limited diagnostic with no human-adjudication flag.

[Phase 8 v1](../evaluation/datasets/phase8-v1.jsonl) materializes 200 new synthetic questions, 40 grouped invoice bundles and exact labeled values and source spans. Seed `868267` determines the content. The manifest freezes the post-training benchmark before model evaluation. It has no human adjudication and cannot serve as retroactive pre-training holdout evidence. The separate v4 cohort preserves the original split and corpus SHA-256. Neither dataset samples naturally occurring documents.
