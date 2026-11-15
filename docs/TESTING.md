# Testing and acceptance plan

The assertions below define full-product acceptance. Native and OCR ingestion, hybrid retrieval, baseline queries, custom endpoints, SSE and the local browser workspace have executable tests. The suite uses Qdrant, Redis/Celery and PostgreSQL with disposable data. Backend model checks load actual pinned weights. The browser fixture uses real retrieval and a synthetic completion endpoint to check transport and UI behavior. Deployment, comparison and research scenarios retain pending cases. Use the [local runbook](LOCAL_DEVELOPMENT.md#phase-3-workspace) for browser and model test flags and [project progress](../PROGRESS.md) for observed results.

## 1. Unit tests

| Area | Required cases |
|---|---|
| PDF coordinates | Page 1-based, rotated pages, normalized top-left boxes, null bbox handling |
| Chunking | hybrid-v3 encoded budgets and overlap, no silent truncation, exact raw source offsets, no cross-document chunks |
| Dense/sparse | Workspace filter; invoice IDs preserved by tokenization; no stale index mixing |
| RRF | Dense rank 1 + sparse rank 2 yields `1/61 + 1/62`; duplicate chunk counted once per branch |
| Reranker | Top 20 candidates reranked, top 5 returned, deterministic tie rules |
| Query policy | Mandatory retrieval for document questions; safe eligible skip for general questions |
| Citation verifier | Reject nonexistent, out-of-scope, wrong page, unsupported quote and stale revision |
| Amount normalizer | INR separators, decimal precision, negatives/credits, missing/unknown currencies |
| Validator | Equal amounts, 100.00 discrepancy, tax differences, rounding, wrong currency, ambiguous match |
| Reflection tokenizer | Each special token roundtrips, saved/reloaded map preserves IDs |
| Reflection loop | Real retrieval call on token, skip on eligible no-retrieve, forced policy override, max 3 calls |
| Ingestion | Idempotent retry, partial OCR quality, empty PDF, unsupported/corrupt input |

## 2. Integration scenarios

**Scenario A: grounded native PDF QA**
1. Upload a fixture PDF containing a known total on page 2.
2. Confirm job succeeded and extraction complete; page 2 image/text are available before indexing. After matched publication, searchable becomes true.
3. Retrieve top-ranked relevant chunk and generate grounded answer.
4. Click citation: navigate to page 2 and highlight source span.
5. Confirm answer mode and trace reflect actual baseline path.

**Scenario B: scanned PDF OCR**
1. Upload image-only scanned invoice fixture.
2. Confirm OCR (not native extraction) origin and geometry are stored.
3. Inspect extracted amount and confidence; if below configured threshold, result must indicate review.

**Scenario C: deterministic cross-document mismatch**
1. Upload PO `PO-100` at `INR 1000.00` and matching invoice `PO-100` at `INR 1100.00`, with the same explicit vendor ID and tax-inclusive grand-total basis.
2. Run investigation.
3. Confirm `AMOUNT_MISMATCH`, `difference=100.00`, `currency=INR` and **two real source anchors**.
4. Confirm report recomputes same difference and cites the exact source.

**Scenario D: ambiguity and unanswerability**
1. Supply two possible POs with same supplier but missing identifiers; do not choose a match without evidence.
2. Ask a fact not contained in any uploaded document. Answer `insufficient_evidence` rather than assert an invented value.

**Scenario E: train/serve boundary**
1. Without checkpoint, call `mode=self_rag` and expect explicit `MODEL_NOT_READY`.
2. Use a simulated decoder fixture to test control-token interception/context reentry; label it simulation. Separately run actual tokenizer/adapter probes and held-out evaluation when a real checkpoint exists.
3. Only a real fine-tuned checkpoint can be used for deployed `self_rag` claims and benchmarking.

**Scenario F: workspace isolation**
1. Index different documents in workspace A and B with similar text/IDs.
2. Query A; inspect every Qdrant, BM25, SQL, citation and report response.
3. Confirm zero B-scope leakage, including cached queries and direct IDs.

**Scenario G: extraction, token limits and publication recovery**
1. With indexing disabled, a native PDF completes extraction and preview while searchable=false. Enabling search requires matching published dense/sparse membership.
2. Place the only answer after reranker content token 256 in a long element; confirm v3 splits it into encoded inputs within budget and preserves its source offsets. Include table headers, Unicode and text whose token count changes after reconstruction. Assert no truncation in v3; historical profile logs all truncation explicitly.
3. Kill the worker/API after object upload, DB commit, queue send, stage writes, Qdrant build, BM25 build and publication commit. Restart and assert a single visible logical result and matching corpus hashes.
4. Deliver a job twice; expire a lease then let its stale worker finish. Only the current attempt can promote content. Exhaust retries and verify explicit failed state plus safe explicit retry.
5. Fail reprocessing/publication: previous preview and published snapshot remain usable, without mixing old sparse/new dense results. Race two publishers; only the expected-parent winner advances the pointer.
6. Delete a document while a run pins it: model input, result reads, report downloads and replay fail closed. Cleanup may retry; cached results and old index snapshots cannot bypass deletion.

**Scenario H: real citations with unsupported claims**
1. Return five real but irrelevant passages; answer abstains despite nonempty top-k.
2. Cite a correct total while claiming payment approval; support fails even though identity and quote checks pass.
3. Swap subtotal/grand-total labels or invoice identities while retaining the same numeric value; support fails.
4. Resolve normalized text through raw offsets, including combining characters and Unicode code points. Incorrect page/version/range fails; valid raw quotes roundtrip exactly.
5. Mixed supported/unknown requested parts produce only verified assertions plus explicit omissions. General unsourced answers are unverified. No semantic support is claimed from a template-only smoke test.

**Scenario I: migration and provenance invariants**
1. Insert existing UUIDs from different workspaces/documents/versions into page/element/chunk/citation/record-link combinations. Composite constraints or validated transactions must reject each invalid combination.
2. Attempt to mutate completed raw text, geometry or source ranges. Reject mutation; reprocessing creates a new version and distinct element IDs.
3. Resolve an old citation after successful reprocessing. It must open its original raw span, not the newest preview.
4. Reject JSON with missing anchors, nonfinite/overflow money, mismatched page or unauthorized evidence on both write and read paths. JSON projections match relational memberships.

**Scenario J: protocol compatibility before annotation**
1. Roundtrip all 15 selfrag-v1 symbols; ensure reserved strings embedded in source data cannot become control events.
2. Verify mandatory -100 masks on context/role headers/padding and trainable target masks. A tiny real training probe confirms gradients on new token rows and equivalent encode/decode/outputs after save/reload.
3. Required-query early Finish/NoRetrieve triggers exactly one evidence attempt; Continue reuses supplied evidence with no retrieval. Eligible NoRetrieve makes zero retrieval calls. No repeated forced retrieval per segment.
4. Simulated failures count against the attempt cap; repeated identical retrieval signatures terminate as cycles. Unexpected EOS/control tokens, missing critics, token/context budget exhaustion and cancellation terminate explicitly.
5. Recompute critic-v1 scores from saved probabilities, test ties, irrelevant/unsupported rejection and independent evidence failure despite a Fully label.
6. Grammar/serializer/token-map mismatch blocks adapter loading and bulk annotation. Passing a simulated state-machine test never enables research mode.

**Scenario K: bounded comparison and independent product release**
1. Match explicit PO/vendor IDs with equal currency and explicit common total basis. Test equality, positive/negative differences and exact finite decimals; recompute persisted finding/report values independently.
2. Missing currency/basis, conflicting amendments, multiple POs, ambiguous identifiers or unreviewed OCR produce needs_review rather than a verified mismatch.
3. Run Phase 3 and Phase 7 acceptance with no adapter installed and Self-RAG disabled. Both products work; research requests return MODEL_NOT_READY. Contract interpretation and fuzzy matching are not represented as implemented.

**Scenario L: one query, replay and cancellation**
1. Concurrent same-key/body POSTs create one run/job; differing bodies conflict. POST returns 202 and polling/GET SSE never creates work.
2. Disconnect and reconnect with Last-Event-ID; replay higher sequences and deduplicate delivery. A cursor after the terminal event returns 204. Invalid/future cursors fail, expired history returns 410.
3. Race cancellation, timeout, worker recovery and finalization; query status/answer/terminal event commit consistently exactly once. Reject a stale lease's write. A restarted worker may recompute deterministic stages; an unknown remote generation step must recover its original response/usage or fail without regeneration. It cannot persist another terminal result.
4. Delete evidence or revoke membership before replay/download; suppress historical content and never append a second terminal outcome to a finished run.

**Scenario M: selected embedding profile compatibility**
1. Load the pinned Gemma text-only configuration; assert unused modality encoders are absent, the output is finite/nonzero, dimension is 768 and normalized norm is approximately 1. Record hardware, dependency versions, memory and latency.
2. Assert query/document prefixes occur exactly once; source text, BM25 input, reranker input and citation excerpts remain unprefixed. Check both tokenizer budgets, including adversarial Unicode and repeated table headers, with truncation disabled.
3. Reject wrong model revision, prompt hash, normalization or dimension against a pinned publication. Equal vector dimensions do not make different models compatible.
4. Rebuild a test MiniLM publication as Gemma in a separate collection, publish matching dense/sparse snapshots, and verify interrupted builds preserve the previous publication. Assert embedding caches cannot cross profile identities.
5. Run a real retrieval smoke test with the selected embedder, retained reranker and generator; missing dependencies fail explicitly. Record Recall@5/source accuracy and latency on the grouped development fixtures; no comparative improvement is assumed.

**Scenario N: custom endpoints, ownership and explicit fallback**
1. Configure a supported ordinary endpoint, validate using synthetic input and run a real baseline query. Verify recorded connection/version/model and resolvable citations. Split Self-RAG additionally requires an admitted fine-tuned runtime with candidate scoring. Generator-only use cannot enable trained reflection.
2. Reject another principal/workspace's connection ID. Ensure secrets never appear in GETs, traces, logs, notebook artifacts or errors; secret-bearing idempotency uses protected keyed hashes.
3. Reject private/metadata/IPv6/redirect/DNS-rebinding endpoint targets before credentials or documents are sent. Only operator-configured local-only loopback may bypass the remote URL rule.
4. Simulate provider timeout, quota, revocation and malformed output. Persist one explicit terminal error, cancel best effort and discard late results; do not switch providers or modes. Explicit selection/new key creates a separate run with its own identity.
5. Rotate credentials/edit endpoint mid-run; stale version cannot dispatch. Deterministic comparison/preview work without a connection, while model generation readiness fails honestly. No automatic notebook start occurs on query submission.

**Scenario O: complete trained runtime over API**
1. Load a real approved base/adapter/tokenizer/token-row release and pass scenario J. Reject missing rows, token-map/manifest mismatch and self-declared unapproved Self-RAG capability. Record deployment trust basis; protocol probes are not proof of remote weights.
2. Run DECIDE -> backend retrieval -> CANDIDATE over the authenticated model-step API. Compare labels and family probabilities with direct pinned inference within declared numeric tolerance. Missing, nonfinite or incomplete probability families fail; ordinary provider logprobs are not presumed sufficient.
3. Enforce the original run deadline, context/segment/token/retrieval budgets across calls and worker restarts. Persist step results/usage. An unknown in-flight result is recovered or the query fails; never blindly regenerate it or reset its budget. Reject duplicate step IDs with changed hashes. After a model-service restart, a request pinned to the previous runtime instance must fail before generation rather than lose deduplication.
4. Check cancellation, source deletion, connection revocation, gateway loss and identity change: no subsequent evidence leaves the backend and late output cannot commit. Two users' contexts/caches remain isolated.
5. Verify a fully local installation with a compatible trained endpoint works without an external provider key. Validate merged/quantized variants independently before admission. Upstream nonstreaming transport must not break backend SSE replay.

The November 15 engineering acceptance uses the explicitly delegated agent operator review in ADR-044. Its ordinary admission check uses the real reviewed runtime and loopback bridge without admission/transport substitution. This is local experimental acceptance; it is not human research validation or certification of untested profiles. [Measured evidence](validation/phase6-validation-2026-11-15.json).

**Scenario P: private Kaggle model bridge (Phase 6K)**
1. Use the configured local account and explicitly start one private owned session for the selected model role. A query never starts a notebook.
2. Verify the checked model identity and authenticated loopback bridge. Notebook execution and file routes must be inaccessible through the bridge.
3. Lose creation responses and repeat lifecycle requests. Reconcile the exact creation receipt and fixed expiry before starting another session.
4. Check quota, expiry, revocation and gateway loss. Keep failures explicit and require a new query for another selected generator.
5. Ctrl-C and expiry request exact-session cancellation. Report accepted cancellation and observed gateway denial separately. Keep uncertain cleanup state for reconciliation.

The existing session/probe tests cover ownership, private state, unknown creation, bounded transport, expiry and cleanup. Local-only model tests cover the role-specific bridge and split query identities. Live model and target-device evidence is recorded in PROGRESS. Public application accounts are outside scope.

### Executable Phase 3 regressions and Phase 4 acceptance

- `backend/tests/test_phase2.py` checks worker-independent queued timeout, event negotiation and result links in expired event responses.
- The Chromium suite verifies lost-receipt upload retry, replay after terminal disconnection, review-version binding, actual endpoint quota loss and stale-content revocation.
- `backend/tests/test_comparison.py` verifies exact matching and arithmetic, negative and out-of-range amounts, uncertainty, scoped PostgreSQL anchors, later review of a pinned source, independent CSV verification and deletion. The Chromium suite compares real unpublished PDFs, opens both source highlights, downloads the exact difference and checks revocation after deletion.
- The disposable migration check upgrades, downgrades and re-upgrades through `0008_reflection_recovery`, then compares the schema. The [runbook](LOCAL_DEVELOPMENT.md#phase-3-workspace) lists model and browser flags.

### Executable Phase 5 pilot checks

- `backend/tests/test_reflection.py` regenerates the frozen manifest, rejects split and source corruption, illegal target sequences and unsupported trajectory commits, and checks empty retrieval, tokenizer roundtrips and loss masks. It also rejects incomplete prediction packets, tampered adapters, reviews of a different checkpoint and fewer than 20 distinct manual cases.
- `scripts/train_reflection.py` checks actual gradient and value changes in the registered rows. It reloads the adapter and tokenizer, compares weights and probe logits, runs one greedy decode and scores validation or explicitly selected test positions. The CPU and CUDA smoke profiles use the open small model. The target Qwen release requires its own checkpoint and manual probes. [Training runbook](LOCAL_DEVELOPMENT.md#phase-5-offline-reflection-training).

### Executable Phase 6 runtime checks

- `backend/tests/test_reflection_runtime.py` covers authentication, body and token budgets, duplicate step replay, hash conflicts, cancellation and runtime instance rotation. Its app integration uses a scripted model service and real pinned retrieval, checks citation resolution, and verifies zero-retrieval and deletion behavior.
- The recovery test exits a real worker process at confirmed and unconfirmed boundaries. It checks resumption without repeating model calls or retrieval, preserves the original token budget and trace prefix, and checks that ambiguous work terminates once. The private journal is erased at termination.
- With `DOCUMIND_TEST_REFLECTION_BUNDLE` pointing to the trained FP32 pilot, the test loads the real adapter and compares direct decision probabilities with authenticated loopback output. It checks a passage-conditioned candidate, rejects a changed manifest or absent token rows, and verifies that the app cannot admit the pilot. The conditional evaluator also scores actual frozen test labels without changing that bundle. Current target-runtime evidence and remaining research gates are in [PROGRESS.md](../PROGRESS.md). [Runtime runbook](LOCAL_DEVELOPMENT.md#phase-6-private-reflection-runtime).

## 3. Evaluation test invariants

- No overlap of grouped source docs between train/validation/test for benchmark tracks requiring generalization.
- No NaN or fabricated scores in missing/zero-retrieval cases.
- One durable terminal outcome per query, ordered persisted sequences and idempotent handling of replayed wire events.
- Repeated evaluation with same frozen configuration records identical source hashes and identifies nondeterminism where present.
- Performance report contains model revision, corpus revision, index revision, hardware and run date.
- No benchmark numbers inserted manually without raw prediction artifacts.

## 4. Definition of done per PR

1. Implementation and corresponding tests are committed together.
2. Relevant unit tests pass locally or CI; unexecuted tests are labeled as unexecuted with reason.
3. Contracts/doc updates accompany changed behavior.
4. No API key or uploaded confidential document is added to Git.
5. New endpoints include validation, scope checks and a failure path.
6. All user-facing claims, citations and numeric findings are backed by inspectable persisted data.

## 5. Release demo script

- Upload native PDF, show processed pages and a real element with a source box.
- Ask a specific fact, show two retrieved passages and a grounded citation.
- Upload PO and invoice, run mismatch investigation, navigate each evidence citation, show deterministic calculation and export.
- If a trained reflection checkpoint exists, show actual `Retrieve` and support decision events; run side-by-side comparison on same question.
- Show evaluation run config and results **only if measured**. Otherwise show clearly marked planned evaluation.

Phase 7 uses the comparison-only Chromium case in the [runbook](LOCAL_DEVELOPMENT.md#phase-4-deterministic-comparison). It runs against the actual API, worker and dispatcher with indexing and inference disabled. It checks upload quota recovery, a lost create receipt, both source links, the exact CSV, and revocation after deletion. The browser injects a recheck response to exercise view invalidation. A separate PostgreSQL test reproduces the actual legacy table rejection, including reads through its evidence links. The two-workspace test creates reports in both workspaces and checks foreign IDs under both URL prefixes. [Observed results](validation/phase7-acceptance-2026-11-15.json).

## 6. Specification-only change validation

Before claiming this documentation amendment complete: resolve all relative Markdown file links; parse every JSON example; verify illustrative quote lengths/offsets; compare .env.example budgets against RETRIEVAL/SELF_RAG contracts and disabled inference/Kaggle gates; verify connection kinds/states, public API fields, model-step boundary and explicit-fallback rules agree with INFERENCE/DATA_MODEL; check the dependency graph has no training edge into the product release; search for superseded ready/partial/512-default/POST-stream rules outside explicitly historical ADRs. Inspect meaningful contracts manually after automated checks.

These checks validate specification consistency only. Executable scenario evidence and remaining product/research gates are tracked in [PROGRESS.md](../PROGRESS.md). No phase acceptance box may be checked based on documentation validation.

## Phase 1B executable checks

`backend/tests/test_extraction.py` generates owned synthetic native/scanned ruled tables, including empty cells, merged native headers, adjacent non-table text, mixed/rotated PDFs and review corrections. PostgreSQL rejects invalid parent/null/array provenance; old source versions remain readable after reviewed versions complete. Review/read deletion and idempotency boundaries retain regression coverage.

Real OCR tests require the locked optional extra plus a verified model root, and skip explicitly without DOCUMIND_TEST_OCR_ROOT. CI provisions this profile and sets the variable; remote CI execution is not claimed locally. See LOCAL_DEVELOPMENT.md. `scripts/evaluate_extraction.py` records actual CER/WER and elapsed time on two clean synthetic English scans, preserving expected/recognized text, model hashes and environment. Those tiny fixtures are diagnostics, not representative accuracy, confidence calibration or a 2-vCPU capacity benchmark.

## Audit findings 1–5 regression coverage

- `test_phase2.py` verifies unresolved table qualifiers/values and a reviewed native-PDF table through new QA and historical result/event/citation reads.
- `test_generation_context.py` uses the actual pinned Qwen tokenizer with explicit scripted decoders. It covers long valid retrieval chunks, complete field retention at the head/tail, source offsets, tokenizer identity, local context refusal and incorrect reported input counts. PostgreSQL/PDF/retrieval tests resolve citations and reject competing tables through the Self-RAG app path. Enable `DOCUMIND_TEST_RETRIEVAL=1` after `scripts/setup_search.py --reflection-tokenizer`. These tests load no target-model weights.
- `test_reflection.py` exercises CLI submission-to-record output through the runtime's review validator, changed bindings/counts, rejected cases, actual wrong generated answers and incomplete evaluation artifacts. Reviewers/verdicts there are synthetic fixtures.
- `test_kaggle_session.py` verifies exact-session Stop after source/version/privacy edits or notebook deletion, plus account/session binding rejection. `test_kaggle_serving_probe.py` checks the template hash and uploaded runtime dependencies. They do not allocate a live GPU.
- `test_reflection_runtime.py` keeps real worker exit/recovery tests and the optional trained CPU pilot. Use the full environment flags in the runbook to avoid optional-model/browser skips. Fresh target-model application and GPU-cleanup measurements remain separate release work.

## Cleanup, retry, CI and selected-model workflow checks

`test_kaggle_session.py` now covers receipt durability before readiness validation, explicit operator-confirmed unknown-create reconciliation, unchanged deadlines and cleanup on expired status despite notebook edits. `test_kaggle_serving_probe.py` checks Start failures entering cleanup and expiry cancellation. `test_runtime_lifetime.py` runs actual subprocesses, kills their launcher and verifies the independent guardian stops startup/descendant processes; tokens are absent from the saved model program. These tests allocate no GPU.

The real browser harness drops an accepted query response and requires identical key/body/run ID on retry. It also checks cached answer removal after `QUERY_RECHECK_REQUIRED`. The CPU runtime test uses the freshly trained CI pilot; `DOCUMIND_REQUIRE_REFLECTION_BUNDLE=1` forbids skipping an absent artifact. Its save/reload and authenticated serving checks are separate from target-model quality. Three bounded paraphrase cases and `test_selected_evaluation.py` verify the frozen application plan and its PDF values.

The explicit operator command `scripts/evaluate_selected_workflow.py` is outside automatic test collection. It allocates an authorized private GPU and measures the complete experimental path with clearly labelled admission/transport injection. The [initial 46-case report](validation/selected-pdf-workflow-2026-11-09.json) and [current 73-case validation](validation/phase6-validation-2026-11-15.json) retain actual results and failures. The latter also records a separate ordinary-admission check without those substitutions, independent target probability comparison, and delegated agent review. No automatic CI job starts a Kaggle session or fabricates human approval. Physical provider GPU deallocation and remote CI execution remain independently unverified.

## Local-only split model acceptance

- Bind the UI, app and published stores to loopback and reject an external API host.
- Validate local generator authentication, configured model identity, message limits and bounded output.
- Verify that supplied candidate text enters the request hash and the trained runtime scores the exact text without rewriting it.
- Pin the generator version and capability check alongside the reflection runtime snapshot. Revoke or edit either destination and block subsequent dispatch.
- Exercise a complete scoped split query and verify the exact source citation. A wrong value still abstains when the critic incorrectly claims full support.
- Interrupt a split query after confirmed work and replay without another generator request. Unconfirmed provider outcomes fail explicitly.
- Test private bridge route allowlisting, local authentication, fixed expiry and exact-session cleanup. Report live Kaggle execution separately.
- Run the reviewed target model and each changed generator/profile through the saved PDF workflow before accepting quality claims.

## Phase 8 controlled evaluation checks

`backend/tests/test_phase8.py` checks the frozen 200-case manifest, original PDF gold spans, preserved pre-training cohort, retrieval variants, independent verification with support scoring disabled, paired statistics, checked archive upload retries and rejection of altered result artifacts. The broader operator run measures both base and trained models on a real private GPU. It is invoked explicitly through the [runbook](LOCAL_DEVELOPMENT.md#phase-8-frozen-benchmark).

A benchmark shard can stop at expiry with a valid partial result. A complete report must include every frozen case exactly once, pass PDF/model/profile checks and recompute scores. The report exits with code 2 while cases are missing. The blinded review packet contains real outputs and empty verdict fields. Generating it supplies zero human judgments. [Measured outcomes and limitations](validation/phase8-benchmark-2026-11-15.md).


## Phase 8 artifact admission and PDF processing

`test_phase8.py` checks paired completeness, explicit failed arms, runtime evidence, source hash binding, retrieval scope and raw candidate scoring. `test_parser_storage.py` runs a real confined child and checks that it can access its private working directory while file and network access outside that boundary fail. Use the real OCR suite to verify the supported platform's model initialization inside the parser workflow. `test_local_models.py` checks pinned checkpoint admission and verified local model loading.
