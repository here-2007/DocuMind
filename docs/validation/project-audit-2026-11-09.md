# Implementation audit through the Phase 6K prototype

Date 2026-11-09. Reviewed the local, single-principal workspace and the experimental model and Kaggle operator paths. Application code was unchanged during this audit.

## Findings 1–5 remediation — 2026-11-09

Findings 1–5 were fixed with permanent regressions. The original reproductions below are retained as history. Findings 6–10 were open at this checkpoint; the follow-up below records their implementation and measured limits.

| Finding | Implemented correction | Regression evidence |
|---|---|---|
| 1 | Shared `evidence-v1.2` rejects recognized table fields before trusting paragraphs. Existing result, event and citation reads recheck pinned support and return `QUERY_RECHECK_REQUIRED` when affected | Reviewed native-PDF table cases through baseline and Self-RAG, legacy supported-answer reads, split labels, identities/currency and unrelated documents in [test_phase2.py](../../backend/tests/test_phase2.py) and [test_generation_context.py](../../backend/tests/test_generation_context.py) |
| 2 | Admit the exact cached generation tokenizer/template. Count serialized metadata and full output reserve, choose explicit contiguous field windows with exact offsets, preflight every step and compare returned input counts. Preserve the immutable bundle's 512-token limit | Both original filler cases and field-at-tail variants with the actual Qwen tokenizer and a scripted decoder; real PDF retrieval/query/citation flow, unfittable local context, wrong runtime counts and confirmed worker recovery |
| 3 | Separate submission and recorded-review schemas. Runtime consumes the CLI record and recomputes its corpus/output bindings and counts | CLI record → the runtime's `load_review` validator round trip, changed counts/hash rejection and rejected-case rejection in [test_reflection.py](../../backend/tests/test_reflection.py) |
| 4 | Require the matching test greedy artifact. Export generated answer, critic labels, finish and recomputed outcomes, mark omitted contexts unrun, bind the artifact SHA to human review and reject acceptance of false full support | Wrong generated value with otherwise correct conditional labels, changed/missing/duplicate evaluation records, and successful export of the selected actual GPU results |
| 5 | Stop authenticates the configured owner and exact persisted session/creation-operation binding independently of mutable notebook metadata. Its operator helper cancels before gateway observation, without a notebook poll prerequisite | Source/version/privacy changes and notebook deletion still cancel only the original session. Changed accounts and mismatched session IDs are rejected in [test_kaggle_session.py](../../backend/tests/test_kaggle_session.py). Eight [operator Stop regressions](../../backend/tests/test_kaggle_serving_probe.py) cover both starting and ready sessions, including idempotent repetition |

The final full suite passed **217 tests, zero skips**, with five PyMuPDF dependency warnings in 123.90 seconds. OCR, real embedding/reranking and retrieval, Chromium, worker recovery and the trained CPU pilot were enabled. Logs/JUnit are `.data/audit-fixes-final-suite.*`. The first expanded run exposed two subprocess tests that did not install their scripted tokenizer fixture in the child process; those fixtures were corrected. Final caller review also exposed the operator helper's poll-before-cancel path; eight new cases reproduced and verified that fix. The complete suite was rerun after these corrections. Ruff, lockfile consistency, whitespace, frontend typechecking and the production build passed. The explicit tokenizer setup option passed the real CPU embedding/reranking probe, and the actual selected bundle's saved tokenizer matched the app's cached tokenizer exactly. No schema or model-weight change was needed.

The selected review packet is `.data/audit-fixes-selected-review-packet.json`, exported from the unchanged selected bundle `d3d40933de2bde1e338f3dca45dd8c838516f40f12e2ab1a3bc2430d50556002` and generated test artifact `3b2ac482e85f7c0af9df115c33b9c7fcf69760629ec1674650bbba01022c46f5`. It contains 36 cases, 96 evaluated episodes and 60 explicitly unrun contexts from the original diagnostic's deduplication/final-decision omissions. No human review was invented. The serializer remains `e1e1a2ac6e555678490871714c811eba59fb2d89d1525c1481dad5ee12ad8079`.

App and runtime must upgrade together for the required `chat_template_sha256` capability. Cache the target tokenizer with `scripts/setup_search.py --reflection-tokenizer`; baseline setup stays independent. The CI cache command was updated for these regressions, while finding 10's trained-adapter CI gap remains open. There was no new Kaggle GPU allocation, live cancellation retest or target-model application-quality measurement during remediation. Those checks and human release acceptance remain pending. Decisions are [ADR-035–037](../../DECISIONS.md#adr-035-fit-explicit-source-windows-with-the-admitted-generation-tokenizer).

## Cleanup, retry, CI and application-evaluation follow-up

The requested follow-up implemented findings 6–10 and ran the unchanged selected adapter through the actual application pipeline. Physical GPU deallocation and production release gates remain independent of this implementation closure.

| Finding | Correction and verification |
|---|---|
| 6 | Start is inside cleanup; operation receipts are durable before readiness/metadata validation. Failure artifacts retain sanitized cleanup status. A completely lost response requires an independently confirmed operation and exact notebook; reconciliation never creates a second session. Creation-failure and lost-response regressions pass |
| 7 | Persist a fixed 20-minute expiry, launch a detached exact-session watcher, and bound installation/loading/model execution in a separate remote process group. Actual subprocess tests kill the launcher and verify expiry terminates descendants. Expired status reconciles cleanup despite notebook edits. The live watcher requested cancellation; gateway revocation was later observed |
| 8 | `evidence-v1.3` supports the exact frozen invoice paraphrases without widening identity scope. The selected model ran through 32 real synthetic PDFs, publication, hybrid search, durable jobs, GPU steps and authorized citation reads. Frozen document QA passed 24/24, including all 12 paraphrases |
| 9 | Unconfirmed query submissions retain key/body/source/destination/config until receipt or explicit new query. The real browser drops an accepted response and confirms retry returns the same run. Cached answers/highlights clear on evidence-recheck rejection |
| 10 | CI installs training dependencies and trains/saves/reloads a bounded CPU pilot from the committed frozen corpus. All 15 rows and authenticated runtime behavior are checked. Required missing artifacts fail instead of skipping. Equivalent local training/runtime verification passed; remote CI execution remains unrun |

Final local verification passed **230 tests, zero skips**, with five dependency warnings in 130.68 seconds. Ruff, lock, whitespace, frontend typecheck/build and local Markdown-link validation passed. Full logs/JUnit are `.data/cleanup-retry-ci-final-suite.*`. The independently selected GPU workflow separately passed its execution/safety assertions and retained three quality failures. [Sanitized measured evidence](selected-pdf-workflow-2026-11-09.json).

The live run measured **43/46 expected outcomes**, **130 model steps**, **zero false verified answers**, **zero transport failures**, and **26 scoped, resolvable citations**. Frozen document cases passed 24/24, supplied references 12/12, abstention cases 5/5 and prompt injection 1/1. Long passages passed 1/2; both arithmetic questions abstained. The long tail window contained the amount but lost the distant invoice ID and received IsREL:No. Arithmetic produced initial Finish decisions. Every document query retrieved once; all 14 eligible queries made zero searches.

This was an isolated experimental application test with explicit release-admission/private-gateway transport injection, preserving the real pilot capability. It did not enable production admission, collect hosted users' keys, fabricate human approval, or retune the model on these outcomes. Original PDFs and raw contexts/results remain private; citation resolution was asserted before the disposable DB was dropped. The source serializer and selected weights remain unchanged.

Next improve long-window identity/evidence handling and eligible-task coverage on fresh grouped data, then perform fresh held-out/application checks and genuine human release review. Finish the per-owner product connection lifecycle and independent resource-release validation. The local watcher requires its host to stay available; retained-state status/reconciliation handles restart. A supplied recovery receipt is operator-attested, not independently API-linked to the notebook. No physical GPU-deallocation, remote CI, hosted multi-user or representative accuracy claim is made.

## Original scope and observed verification

Read the architecture, milestone and evidence/inference contracts, entry points, job/recovery paths, scoped retrieval, comparisons, model protocol, training/evaluation/review code, Kaggle controller and probe, and frontend forwarding/query flows. The structural graph was dated 2026-11-07 and reported the implementation paths as untracked. Conclusions use direct source reads and executable checks.

- Full local suite passed 185 tests with zero skips and five dependency warnings in 128.60 seconds. OCR, actual retrieval, Chromium, worker recovery and the trained CPU pilot were enabled. Logs and JUnit are in ignored `.data/project-audit-suite.*`.
- Ruff, lockfile consistency, whitespace, frontend TypeScript checking and the production frontend build passed.
- Verified the selected v3 bundle's nine file hashes, token map, corpus identity and 36-case review packet. The recorded 24/24 copied values and 0/48 false full-support claims reproduce from the saved synthetic results.
- Targeted Python and JavaScript reproducers exposed the discrepancies below. No remote GPU sessions were created or modified for this audit.

At the original audit, the suite passed because these specific integration and failure cases were absent or enforced the wrong expectation. The remediation above adds coverage for findings 1–5.

## Original must-fix findings — 1–5 now resolved

### 1. P1 — QA ignores competing table fields

The shared QA verifier removes all non-paragraph elements before checking identities and contradictory values. A paragraph containing `Grand total: INR 1100.00` and a table cell containing `Grand total: INR 2200.00` returned `verified` with the paragraph quote. Baseline QA and the trained loop share this verifier. The comparison rule already has a separate table guard.

Reject verification when the selected source contains a competing or incomplete table field relevant to the claim. Add an integration regression for reviewed table sources, and address previously stored answers affected by the new verifier rule. Otherwise the app can label an unresolved value as supported. [Source](../../backend/documind/evidence.py#L77) · [Contract](../EVIDENCE.md).

### 2. P1 — Retrieved chunks do not fit the selected model's budget

The application filters passages by a 12,000-byte ceiling. The selected model bundle has a 512-token context. Its decoder reserves 132 candidate tokens in addition to the serialized prompt. A valid 239-token reranker passage containing an invoice total and repeated `document processing` encoded to 391 Qwen input tokens. The real decoder returned `context_limit` and zero output tokens before any forward pass. Numeric text produced a 575-token input.

Count the full prompt using the exact generation tokenizer, include evidence metadata and committed segments, and preserve answer spans when fitting evidence. Alternatively validate and publish a deliberately larger context configuration. Run this through real retrieval and the target runtime. Otherwise ordinary permitted chunks can cause abstention despite available evidence. [Application boundary](../../backend/documind/reflection_queries.py#L225) · [Decoder](../../backend/documind/reflection_runtime.py#L179).

### 3. P1 — Recorded review output cannot be loaded by the runtime

The review CLI writes `corpus_manifest_sha256`, `accepted_cases`, `rejected_cases` and `release_status` in addition to the submission fields. The runtime parses the file as strict `ManualReview`, which forbids those fields. Passing a recorded review through that schema reproduced four extra-field validation errors.

Define and validate a shared recorded-review representation, or explicitly support and document the submission representation at the runtime boundary. Add a CLI-output-to-runtime-input regression. Otherwise a completed review record fails the release workflow. [Recorded output](../../backend/documind/reflection_review.py#L151) · [Runtime input](../../backend/documind/reflection_runtime.py#L68).

### 4. P1 — Human review omits actual generated answers

The 36-case packet contains only `state`, `context`, `target` and `predictions` per episode. The target is gold text. Predictions are reflection labels scored with gold prefixes. The packet contains no greedy answer text, generated-answer verification outcome or binding to the greedy output artifact.

Include the actual generated answers, labels and support outcomes in the review packet and bind the reviewed output digest to release admission. Keep gold targets visibly distinct. Otherwise human acceptance can review correct gold examples while missing incorrect generated values, the exact failure observed in the earlier adapter. [Packet assembly](../../backend/documind/reflection_review.py#L108) · [Greedy evaluator](../../scripts/evaluate_reflection_v3.py).

### 5. P1 — Editing an owned notebook prevents Stop

After starting a private owned session, incrementing that notebook's current version from 1 to 2 caused `stop()` to raise `KAGGLE_OWNERSHIP_CHANGED`. No cancellation call occurred. Stop validates the current notebook source/version before using the already recorded exact session ID. The existing regression explicitly expects this rejection.

Preserve the creation-time ownership and resource binding for cleanup. Separate the checks for starting/promoting a changed template from authorization to cancel its old owned session. Otherwise a normal notebook edit can strand the previous GPU session until manual cleanup. [Ownership check](../../backend/documind/kaggle_session.py#L86) · [Stop](../../backend/documind/kaggle_session.py#L229).

## Original follow-up findings — 6–10 implemented with limits above

### 6. P2 — Creation errors bypass the runner's cleanup path

`run()` calls `controller.start()` before entering its `try/finally`. A controlled reproduction that allocated a session and then raised `KAGGLE_SESSION_ID_UNAVAILABLE` recorded allocation but no cleanup attempt. The controller writes `create_pending` before the remote request, then stores the returned operation only after validating its metadata. An uncertain response remains visible but has no automatic reconciliation.

Cover session creation with the cleanup/reconciliation path, persist recoverable operation identity promptly, and expose a concrete way to reconcile an unknown create. Preserve duplicate-create prevention. Otherwise failures at allocation can consume quota outside the normal cleanup path. [Runner](../../scripts/kaggle_serving_probe.py#L280) · [Controller](../../backend/documind/kaggle_session.py#L157).

### 7. P2 — The probe expiry timer does not release its GPU

The ten-minute timer only sets `server.should_exit = True`. It starts after installation and model loading. The interactive Python kernel and its decoder/model references remain alive, so the timer neither bounds startup nor establishes GPU release if the operator process disappears.

Provide a session lifetime mechanism and reconcile exact-session cancellation. Distinguish model HTTP shutdown from notebook/kernel shutdown and allocation cleanup in tests and documentation. Otherwise a lost operator process can leave an idle GPU allocation. [Bootstrap](../../scripts/kaggle_serving_probe.py#L129).

### 8. P2 — Half of the document holdout is outside the verifier grammar

Of the 24 required-evidence v3 test questions, 12 use `For invoice ..., give the ...`. The current `field_question()` rejects every one of those questions. The greedy evaluator injects gold evidence directly and never runs the application verifier, so these failures are absent from its perfect copy score.

Add an application-level evaluation alongside the model episode diagnostic. Align supported question forms through a versioned rule change or explicitly exclude unsupported forms from the application acceptance set. Include actual retrieval, generated context, verifier outcomes and resolvable citations. Otherwise model scores can be mistaken for working document QA. [Corpus](../../backend/documind/reflection_corpus_v3.py#L94) · [Evaluator](../../scripts/evaluate_reflection_v3.py#L39) · [Verifier](../../backend/documind/evidence.py#L77).

### 9. P2 — Query retries can create duplicate generation jobs

The frontend's generic mutation helper creates a new idempotency key for every call. If the server accepts a query but the response is lost, the form retains the question and has no receipt. Repeating Ask submits the same logical query with a different key. A JavaScript reproduction confirmed two distinct keys across this retry.

Preserve the query payload and key until acceptance is known, following the existing upload retry approach. Make changing the logical operation explicit. Otherwise a response loss can create another model call and a second result with possible provider charges. [Mutation helper](../../frontend/lib/api.ts#L64) · [Query submission](../../frontend/components/query-chat.tsx#L94).

### 10. P2 — CI skips the real trained runtime check

The workflow installs OCR/retrieval extras and downloads the base smoke model. It does not train/provision a PEFT pilot or set `DOCUMIND_TEST_REFLECTION_BUNDLE`. The actual adapter service test always skips in that configuration. Removing the variable reproduced the skip.

Add a bounded CPU train/save/reload and authenticated runtime check with the training dependencies. Make missing required artifacts fail in that CI job. Keep GPU research runs separately recorded. Otherwise changes to adapter loading or inference can pass CI through scripted responders alone. [Workflow](../../.github/workflows/tests.yml#L17) · [Skipped test](../../backend/tests/test_reflection_runtime.py#L490).

## Original milestone interpretation

| Area | Audit interpretation |
|---|---|
| Phases 0–4 | Substantial working local implementation with passing integration and browser checks. Findings 1 and 9 affect correctness/recovery here |
| Phase 5 | Real immutable experimental adapter and reproducible synthetic results. Human review workflow needs findings 3 and 4 |
| Phase 6 | Service and durable loop implemented. Generation budgets and actual target-model-to-application evaluation still need findings 2 and 8 |
| Phase 6K | Live private operator proof, with recorded authentication and replay. Cleanup reliability and product credential/lifecycle integration remain open |
| Phases 7–8 | Pending product release/reporting work and broader controlled research evaluation |

The private Jupyter gateway remains a separate operator path. Current application connection requests accept ordinary custom endpoints, and application Self-RAG uses a configured origin that rejects path-bearing gateway URLs. That is an acknowledged product integration gap, not evidence that the operator test failed.

## Recommended execution order

1. Completed: fix findings 1–5 with regressions through the normal service and review entry points, as recorded above.
2. Completed within the operator/local scope: creation/expiry cleanup, query retry handling and required real adapter CI checks. Physical GPU deallocation and remote CI execution remain unverified.
3. Completed the selected-model PDF-to-answer diagnostic. Improve the retained tail-field/arithmetic failures using fresh grouped development data and a new holdout; preserve these frozen results.
4. Review at least 20 cases only after the packet exposes actual generated outcomes. Bind the accepted evidence to a release, then verify the approved runtime deployment.
5. Finish the per-owner Kaggle connection lifecycle and browser controls. Progress the independent Phase 7 reporting, quotas and release workflow alongside this work.
6. Run the Phase 8 same-base comparison and retrieval ablations on frozen research data, followed by clean setup and measured capacity checks.

## Original audit limits

This was a risk-focused repository audit, not a line-by-line proof or a new GPU benchmark. OCR/model dependency internals, a fresh remote CI run, hosted multi-user authorization, load/capacity and live GPU deallocation were not validated. Remote serving claims were checked against saved artifacts. The selected adapter's synthetic measurements remain valid within their recorded scope. No human acceptance was fabricated.
