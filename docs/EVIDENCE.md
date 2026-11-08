# Evidence and deterministic comparison contract

This contract owns claim verification and comparison semantics. It is a design, not an implemented verifier or a claim of perfect semantic entailment. Lifecycle/enumeration ownership remains in [DATA_MODEL.md](DATA_MODEL.md); requirements below apply to baseline, comparison and trained Self-RAG equally.

## 1. Three independent checks

For each candidate claim, persist its answer character span, evidence anchors, check results, verifier version and any calculation ID. Offsets use zero-based Unicode code points, end exclusive; the frontend converts to JavaScript UTF-16 offsets when highlighting.

1. **Identity/access:** source IDs exist in the authorized workspace, pinned extraction/publication, and evidence actually supplied to this generation or deterministic calculation. Recheck deletion and access before generation, finalization and every later answer/report read. Failure rejects the reference; do not merely downgrade an unauthorized citation and return its content.
2. **Span:** `quote` is the exact `text_raw[start_char:end_char]` of the identified immutable element. Normalized matching must first resolve back to these raw offsets. Multi-element claims use multiple citations. Correct page, element, version and source locator must agree.
3. **Support:** evidence establishes the specific proposition, including entity, field, amount, currency and qualifiers. Record `supported`, `unsupported` or `unknown` per claim. A real quote, a high retrieval score or `[IsSUP:Fully]` alone cannot pass this check.

Example: `Total INR 1,100.00` supports a verified extraction of that document's total. It does not establish that an invoice was approved, paid, fraudulent or contract-compliant. Even five retrieved hits with valid quotes must yield abstention on those unsupported questions.

## 2. Bounded baseline verifier: `evidence-v1`

- Initially support direct field questions (document identifiers, explicitly labeled dates/totals/currency) only when a deterministic query-to-field rule resolves the requested field and document, and a deterministic parser or human-reviewed field establishes the value. Freeze the supported rules and their tests with the implementation. No successful rule means `unknown`, not a best-guess answer.
- Preserve native extraction confidence as null when unavailable. Do not convert null to certainty. For OCR, a field is eligible only after human review or a versioned, calibrated extraction rule; until that rule has measured thresholds, require human review. Contradictory values, ambiguous labels and incomplete table context block verification.
- Generate direct answers from verified field templates and calculation records. An LLM may select candidate evidence or propose prose, but output validation must check every factual proposition against the verified claim set. If that mapping is ambiguous, use the deterministic rendering or abstain. The LLM cannot introduce a new factual assertion through wording.
- Other questions may show clearly labeled source excerpts as navigation aids, but these are not represented as supported answers. General knowledge without sources is `unverified` and has no invented citations.
- A later semantic verifier requires a versioned evaluator, a held-out entailment/contradiction set, measured error rates, and explicit uncertainty. It does not replace identity/span checks or the arithmetic validator.

## 3. Answer decisions

- `supported`: every emitted factual claim has passed all applicable checks and all requested parts are answered.
- `partially_supported`: only verified claims are asserted; unanswered parts are explicitly identified. Never include unsupported factual prose with a generic disclaimer.
- `insufficient_evidence`: no requested document claim can be verified; return abstention. Suggestions/source previews are separate from answer citations.
- `unverified`: eligible general knowledge or explicitly requested exploratory output outside the verified claim set. Never use this status to leak rejected sources.

Irrelevant hits and missing facts are handled even when top-k is full. Ranking scores order candidates; they are not calibrated confidence probabilities. Any later rejection threshold is tuned on development data, versioned, and evaluated on answerable and unanswerable cases. A wrong-document total and a correct number attached to the wrong field must fail support checks.

## 4. First comparison rule: `grand_total_match_v1`

Scope: one invoice and one purchase order, with exact explicit PO reference and matching explicit vendor identifier; both source documents are selected and authorized. Missing/duplicate identifiers or multiple candidate POs produce `UNVERIFIED_MATCH` and `needs_review`. Name similarity alone is not a match. Normalize identifier whitespace/Unicode conservatively; do not erase punctuation or leading zeros.

Both explicitly labeled grand totals must have verified source spans, identical known ISO currency, the same explicitly established total basis (for example tax-inclusive), and no unresolved amendments, credits or conflicting values. Missing basis/currency or ambiguous extraction produces `needs_review`, without asserting an amount mismatch. The initial synthetic fixtures explicitly state their common basis and currency.

Parse decimal strings directly into `Decimal`, require finite values representable in `numeric(20,6)`, and compute `difference = invoice_total - purchase_order_total` without intermediate rounding. V1 uses exact equality (tolerance `0`); display formatting must not alter stored inputs/result. Persist original values, normalized inputs, basis, operation, rule version, linkage method and both evidence anchors. Difference zero gives a verified match; otherwise `AMOUNT_MISMATCH`. Test negative values and precision overflow explicitly. No currency conversion, inferred tax adjustment or amendment precedence exists in v1.

Structured retrieval of these records and source elements is mandatory and logged even when no dense search is necessary. Findings/reports use `mode=deterministic_comparison`; an LLM explanation cannot change the calculated values. Contract terms, line items, taxes, fuzzy matching and amendments are later versioned rules with independent fixtures; the first product release does not claim to validate them.

## 5. Gates

Before baseline QA: tests reject a real quote attached to an unsupported approval claim, wrong field, wrong document, unauthorized source and a nonempty irrelevant result set. Before comparison: tests cover equal/unequal totals, missing currency/basis, ambiguous matches, low-confidence OCR, exact decimals and report equality. See [TESTING.md](TESTING.md) scenarios H and K. These are required tests to implement, not passed results.
