# Evidence and deterministic comparison contract

This contract owns claim verification and comparison semantics. Phase 2 implements the bounded evidence-v1 verifier. Phase 4 implements the first comparison rule below. [DATA_MODEL.md](DATA_MODEL.md) owns lifecycle and stored values.

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

The workspace accepts two or three distinct authorized PDFs and pins their preview versions. A verified result requires exactly one invoice and one purchase order. Missing or duplicate identifiers and competing candidates produce `UNVERIFIED_MATCH` and `needs_review`. Match PO and vendor identifiers case-sensitively after trimming and Unicode normalization, preserving punctuation and leading zeros.

The parser reads bounded English labels on individual lines of complete paragraph elements. Use `Document type: Invoice` or `Document type: Purchase order`, `PO reference` or `PO number`, `Vendor ID`, `Grand total: INR 1,100.00` and `Grand total basis: tax-inclusive`. An explicit supported currency may also appear on a separate `Currency` line. Both totals need the same explicit currency and basis, with exact spans. Unreviewed sources, competing values, unsupported formats and recognized amendment or credit indicators produce a needs-review outcome. The total-specific outcome is `UNVERIFIED_TOTAL`.

A recognized comparison label in a table cell makes the investigation require review, including in a manually reviewed extraction. The paragraph-only rule cannot resolve table layout or competing values there. Earlier findings, their evidence links and their report creation, metadata and download routes return `INVESTIGATION_RECHECK_REQUIRED` for affected sources.

Parse decimal strings directly into `Decimal`. Inputs must be finite and fit `numeric(20,6)`. Compute `difference = invoice_total - purchase_order_total` without intermediate rounding and store it as `numeric(21,6)`. Zero gives `AMOUNT_MATCH`. A nonzero value gives `AMOUNT_MISMATCH`. Preserve typed inputs and exact raw anchors for the amounts, IDs, currency and basis. The finding and CSV expose the operation, tolerance, rule version, linkage method and both grand-total anchors. The rule supports negative values and up to six fractional digits.

The worker retrieves typed records and exact source spans through scoped PostgreSQL reads and logs structured completion. Result and CSV reads recheck field support and arithmetic independently. Findings and reports use `mode=deterministic_comparison`. Contract terms, line items, conversion, tax recomputation, fuzzy matching and amendment precedence need later versioned rules and fixtures.

## 5. Gates

Before baseline QA: tests reject a real quote attached to an unsupported approval claim, wrong field, wrong document, unauthorized source and a nonempty irrelevant result set. Before comparison: tests cover equal/unequal totals, missing currency/basis, ambiguous matches, low-confidence OCR, exact decimals and report equality. See [TESTING.md](TESTING.md) scenarios H and K. These are required tests to implement, not passed results.

## Implemented Phase 2 rules

The `evidence-v1.3` verifier matches a bounded question grammar for explicit invoice and purchase order identifiers, invoice and due dates, currency, grand total and amount due. An explicit invoice identifier must resolve to one labeled invoice in the pinned scope. A question without an identifier needs a single selected document. Multiple invoice identities on that source block verification. Totals use explicit supported ISO currencies and bounded Decimal parsing. Dates must be valid ISO dates. Retain recognized field labels with unparsed values as uncertainty, including competing amounts in an unsupported number format. Conflicting values, incomplete table context, an unsupported question, or a field missing from the retrieved raw spans yields abstention.

A recognized QA field label in a table cell on the resolved document returns `unsupported_table_fields`, even after extraction review. Labels and values may occupy different cells, so a label without an inline value also blocks paragraph-only verification. The guard covers invoice identity and currency qualifiers as well as amounts and dates. Baseline and Self-RAG use the same check against complete pinned source elements, including content outside a model's evidence window. Unrelated documents outside the resolved claim do not block it.

Version 1.3 additionally accepts the exact bounded form `For invoice <identifier>, give the total.`, and its `currency`/`due date` variants. Match the whole question and resolve the same explicit invoice identity; appended instructions or additional identifiers do not broaden the grammar. This aligns the frozen v3 paraphrases with the application verifier without changing labels, corpus or model weights.

Stored answers, event reads and citation reads recheck support against those pinned sources. A previously supported answer that fails the current verifier returns `409 QUERY_RECHECK_REQUIRED`; start a fresh query to obtain the current abstention. Its historical result is retained privately rather than silently rewritten.

A supported answer returns the verified field line. The selected chat endpoint can see the question and that verified excerpt. Its candidate is accepted only if it matches the independently verified line. The result records candidate acceptance and whether generation was used. A different candidate leaves the verified line in the answer. Transport failures and identity changes produce terminal errors. Query and citation records retain the exact Unicode span and verifier version.
