# Testing and acceptance plan

The assertions below define full-product acceptance. The native parser/storage/ingestion subset is executable in backend/tests; later retrieval/model scenarios remain tests to implement. Actual results are recorded in [PROGRESS.md](../PROGRESS.md). Use pytest for Python; frontend unit/component tests and an end-to-end browser test runner where appropriate. Keep tiny synthetic fixture documents under a legally distributable test fixtures directory only after they are created.

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
1. Configure a supported ordinary endpoint, validate using synthetic input and run a real baseline query. Verify recorded connection/version/model and resolvable citations; Self-RAG requests fail with MODEL_NOT_READY. No local Llama is loaded for ordinary endpoint inference.
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

**Scenario P: managed Kaggle lifecycle (Phase 6K)**
1. With the feature disabled, start returns FEATURE_DISABLED and never uses configured developer credentials. Before a live test, record platform permission/access and secure credential-delivery checks.
2. With authorized access, create only a private owned notebook, load the approved release and expose an authenticated inference-only gateway. Never persist credentials or user document text in notebook source/output. Mark ready only after compatibility probes.
3. Lose the create response, duplicate delivery and expire worker leases; reconcile one owned resource and reject stale promotion. Validate/stop/edit races cannot revive old or revoked configurations.
4. Exhaust quota, expire session, revoke credentials and break the tunnel; show unavailable with accurate reason (unknown quota stays unknown), terminate affected work explicitly and offer user-selected custom-endpoint setup for a new run.
5. Stop/revoke schedules confirmed remote cleanup; unreachable cleanup remains pending/failed and visible. Never remove unrelated notebooks. Immediate credential erasure warns of remote cleanup that can no longer be confirmed.

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

## 6. Specification-only change validation

Before claiming this documentation amendment complete: resolve all relative Markdown file links; parse every JSON example; verify illustrative quote lengths/offsets; compare .env.example budgets against RETRIEVAL/SELF_RAG contracts and disabled inference/Kaggle gates; verify connection kinds/states, public API fields, model-step boundary and explicit-fallback rules agree with INFERENCE/DATA_MODEL; check the dependency graph has no training edge into the product release; search for superseded ready/partial/512-default/POST-stream rules outside explicitly historical ADRs. Inspect meaningful contracts manually after automated checks.

These checks validate specification consistency only. The native subset has executable tests and local run evidence in [PROGRESS.md](../PROGRESS.md); full scenarios A-P, model probes and later-service fault injection remain pending. No phase acceptance box may be checked based on documentation validation.

## Phase 1B executable checks

`backend/tests/test_extraction.py` generates owned synthetic native/scanned ruled tables, including empty cells, merged native headers, adjacent non-table text, mixed/rotated PDFs and review corrections. PostgreSQL rejects invalid parent/null/array provenance; old source versions remain readable after reviewed versions complete. Review/read deletion and idempotency boundaries retain regression coverage.

Real OCR tests require the locked optional extra plus a verified model root, and skip explicitly without DOCUMIND_TEST_OCR_ROOT. CI provisions this profile and sets the variable; remote CI execution is not claimed locally. See LOCAL_DEVELOPMENT.md. `scripts/evaluate_extraction.py` records actual CER/WER and elapsed time on two clean synthetic English scans, preserving expected/recognized text, model hashes and environment. Those tiny fixtures are diagnostics, not representative accuracy, confidence calibration or a 2-vCPU capacity benchmark.
