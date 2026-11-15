# Project progress

Current implementation status and dated validation. [MEMORY.md](MEMORY.md) holds durable lessons; [DECISIONS.md](DECISIONS.md) holds reasoning; [IMPLEMENTATION.md](IMPLEMENTATION.md) owns milestone scope and acceptance gates. Update this file after meaningful work; historical checks are not a new test run.

## Current state

The local app covers PDF upload, native/OCR extraction, ruled tables, immutable review, hybrid publication, cited baseline QA and exact invoice/PO grand-total comparisons through the Next.js workspace. Comparison findings open both sources and export a checked CSV. Chat supports replayable traces, cancellation, citation highlights and explicit custom endpoint selection.

| Milestone | State |
|---|---|
| Phase 0: backend, containers, migrations and test harness | Verified locally; request logging regression fixed |
| Phase 1A: native-PDF ingestion and source preview | Verified locally; deletion/read and pagination regressions fixed |
| Phase 1B: OCR and table adapters | Six audit fixes verified locally. OCR and table results require human review |
| Phase 2: hybrid-v3 retrieval and custom-endpoint baseline | Six audit fixes verified locally with permanent regressions |
| Phase 3: Next.js workspace and SSE traces | Six workflow findings and both event API discrepancies fixed and verified locally |
| Phase 4: exact invoice/PO grand-total comparison | Reverified locally. Findings, report creation, metadata and download enforce the same integrity checks |
| Phase 5: annotations and QLoRA | Training/reload verified. Human v3 and explicitly delegated agent v4 reviews remain distinct |
| Phase 6: trained Self-RAG runtime | Final operator review accepted opt-in v4 on the tested private Kaggle profile. Local trained FP16 MPS did not pass on the tested Mac |
| Phase 7: product reporting and release | Normal local comparison and CSV acceptance passed. All 12 reproduced code defects have fixes and passing regressions in the local working tree |
| Phase 6K: private Kaggle model execution | Live bridges and technical lifecycle verified. Five identified sessions cancelled with gateway revocation observed; physical GPU release is not independently observable |
| Phase 8: combined research evaluation | Controlled synthetic evaluation complete. Representative pre-training data, human review and broader research release remain open |

OCR, indexing and endpoint generation are opt-in. Default Compose stays native-only. The host-process setup uses PostgreSQL, Redis/Celery and Qdrant from Compose. [Local development](docs/LOCAL_DEVELOPMENT.md) lists routes and commands.

## Audit findings 1–7 remediation — 2026-11-16

Findings 1–7 are fixed in the local working tree with permanent regressions. Qdrant requires a private key and has CORS disabled. Page writes and physical cleanup share a nonblocking document lock. Reflection calls have persisted per-step deadlines and exact-step cancellation on transport/response failure. The process guardian kills descendants on normal/error exit. Readiness and repository-backed HTTP routes reject an incompatible migration revision. Index cleanup retries lock contention without waiting beyond its lease. Connection create/edit recovers lost receipts using the same transient payload and key, including an authentication interruption during recovery.

The final suite passed **310 tests with zero skips** and five existing PyMuPDF dependency warnings. It includes real PostgreSQL/Redis/Qdrant, cached OCR/retrieval and chat weights, trained CPU pilot reload/runtime checks, populated migration upgrades and both Chromium modes. Ruff, frontend typecheck/production build, offline lockfile and whitespace checks passed. A fresh isolated native Compose install passed upload, preview, immutable review and physical deletion smoke checks and removed its containers, volumes and image. [Fixes, source bindings and evidence](docs/validation/audit-fixes-1-7-2026-11-16.json).

The existing private configuration received only its missing Qdrant key. Only the Qdrant service was recreated, retaining its volume and unchanged collection inventory. No credentials were printed. The persistent database remains `0009_split_reflection`; it now fails admission with `DATABASE_MIGRATION_REQUIRED`. Before using this updated host installation, run the [documented paired-database upgrade](docs/LOCAL_DEVELOPMENT.md#local-only-model-setup). No automatic migration was performed on persistent data.

Findings **8–15 remain open**, including evaluation report admission/scoring, parser isolation, uncertain Kaggle cleanup, dependency advisories and publication. No target GPU/MPS quality run, retraining, provider cleanup reconciliation or remote CI ran for these fixes. The original [deep audit](docs/validation/deep-audit-2026-11-16.md) and its 297-test result remain historical evidence.

## Deep audit — 2026-11-16 (historical pre-fix snapshot)

The review identified **15 actionable findings**. Findings 1–7 are addressed above. Findings 8–11 concern incomplete QA arms, missing runtime/code identity evidence, retrieval questions losing their intended source scope and raw candidate text discarded after filtering. Findings 12–15 concern host parser isolation, an unknown Kaggle creation/cleanup outcome, two dependency advisories and the unpublished working tree. [Original ranked findings](docs/validation/deep-audit-2026-11-16.md) · [Original evidence bindings](docs/validation/deep-audit-2026-11-16.json).

The audit's original suite passed **297 tests with zero skips** and five dependency warnings. Eight disposable Python probes, a Chromium receipt-loss probe and saved-report checks exposed gaps outside that suite. The audit itself made no application fixes or GPU allocations and left persistent metadata at `0009_split_reflection`. The frontend production scan reported zero advisories; the Python scan reported two. Model quality and broader research release limitations remain independent of this remediation.

## Phase 8 frozen evaluation — 2026-11-15

The frozen 200-case `phase8-v1` benchmark scored **172/200 baseline** and **162/200 trained Self-RAG** answers correct. Both arms use the pinned Qwen revision and NF4 on a Tesla T4 with the same 128-token output ceiling. Citations matched gold at **110/110** and **88/88**, with recall of **110/120** and **88/120**. Median/p95 latency was **1.42/2.52 s** for baseline and **6.18/17.17 s** for trained inference. Self-RAG skipped retrieval in all 40 eligible cases and answered **34/40** correctly. The support-critic usage ablation changed zero final answers. The paired accuracy difference was −5 points, with a grouped bootstrap 95% interval of [−11, +1.5]. [Measured report](docs/validation/phase8-benchmark-2026-11-15.md) · [Artifact bindings](docs/validation/phase8-benchmark-2026-11-15.json).

The separate, previously used 60-case v4 pre-training cohort scored **45/60 baseline** and **60/60 trained**, with 24/24 gold citations for both and 36/36 correct trained skips. Broader retrieval ablations returned the gold span in 36/120 dense, 68/120 BM25, 65/120 hybrid RRF and 58/120 reranked queries over all 80 documents. The paired QA arms used their declared source selections. No model transport errors occurred in the 260 retained cases. Failure traces remain available, including all 32 broader document coverage misses.

The implementation adds the frozen dataset, paired GPU operator/evaluator, separate document checks, checked reporting and 13 regression tests. `selfrag.py` exposes offline support/budget controls and `search_index.py` exposes ranking without a reranker, with existing production defaults. Separate document checks passed 6/6 comparison outcomes, 72/72 key fields and 12/12 native/scanned table cells. Three OCR fixtures had zero edit errors across 228 characters and 36 words. The final suite passed **297 tests with zero skips** and five dependency warnings. Both Chromium modes, trained CPU pilot, populated migration, Ruff, lockfile and whitespace checks passed. A clean native Compose smoke passed and removed its disposable resources. A private local source snapshot was cloned, all 175 source hashes matched and its 13 evaluation tests passed using the existing locked cached environment. Partial and complete report commands returned codes 2 and 0, respectively. The persistent database stayed at `0009_split_reflection`.

The manifest was created after checkpoint selection and frozen before scoring. Its 40 groups, 80 PDFs and 200 cases are synthetic and automatically labeled. The first deployment receipt check and initial harness pass exposed API/context problems, whose raw failures remain private. An adapter upload failed before inference and its session was cancelled. Kaggle accepted cancellation for five identified sessions, and one returned gateway 404. One creation without an operation/session receipt remains `create_pending` with an owned notebook version observed as Error. It needs reconciliation. All sessions retain their original expiry. Physical GPU release and dollar costs are unobservable from these records.

Twenty actual cases now have a blinded review packet, exact PDFs, a separate key and an empty verdict CSV under `.data/phase8-2026-11-15/report-broad/`. Blinded human verdicts, RAGAS and a representative 200-case pre-training holdout remain open. Publication and remote CI remain unverified. Private raw artifacts, run configurations, models and credentials stay outside Git. [Protocol and commands](docs/EVALUATION.md#10-phase-8-controlled-comparison).

## Audit remediation through Phase 7 — 2026-11-15

The original 265-test audit found twelve defects with additional disposable probes. Findings 1–12 now have regression coverage for source and preview integrity, deletion recovery, send deduplication, accepted uploads after a lower limit, comparison submission recovery, temporary generator load, missing workspace initialization, conditional scores, reference repetition, DNS deadlines and Kaggle expiry cleanup. [Original findings](docs/validation/audit-through-phase7-2026-11-15.md) · [Remediation evidence](docs/validation/audit-fixes-12-2026-11-15.json).

The final suite passed **284 tests with zero skips** and five dependency warnings. Both Chromium flows passed, including retry after a failed comparison detail read and a deletion cleanup retry after browser reload. Ruff, frontend typecheck and production build, lockfile verification, a fresh native Compose smoke test, Alembic schema comparison and the populated Phase 2 upgrade passed. The existing v4 artifacts passed the stricter review checks for 324 conditional predictions and 20 recorded verdicts. The Kaggle retry tests used local fault injection.

The persistent database stayed at `0009_split_reflection`. Existing host installations need to migrate to `0010_artifacts_dispatch` before switching to this code. Isolated Compose resources were removed. Live target GPU/MPS checks, fresh weight downloads, the separate dependency and documentation follow-ups, and publication/remote CI remain open. [Migration and startup commands](docs/LOCAL_DEVELOPMENT.md#local-only-model-setup).

## Phase 7 local product release — 2026-11-15

**Phase 7 passed its local acceptance gates.** The production browser build uploaded two PDFs, opened both source highlights and exported the exact INR 100.00 difference with indexing, generation and trained reflection disabled. Dropping the accepted create receipt and retrying recovered the same investigation. An explicit recheck cleared the stale view. Confirmed deletion revoked the report and released an upload slot after cleanup. The release retains source-cited CSV as its report format. [Measured acceptance](docs/validation/phase7-acceptance-2026-11-15.json).

The configurable 200-document quota serializes new admissions under the workspace lock and preserves accepted replays at capacity. Two populated workspaces produced different verified results from matching PO and vendor IDs. Both directions rejected foreign investigations, findings, evidence, report metadata and CSV bytes under either URL prefix, and rejected mixed source selections.

The full suite passed **265 tests with zero skips** and five dependency warnings. It ran Chromium with a synthetic chat endpoint and again with inference disabled, plus PostgreSQL/Celery recovery, OCR, pinned retrieval, the trained CPU pilot and index rebuild/identity checks. Ruff, frontend typecheck/build, lockfile and Alembic checks passed. The first quota test assertion was corrected to measure newly written files in the shared fixture store. Phase 8 remains open.

## Final model review and local setup acceptance — 2026-11-15

**Qwen v4 is accepted for opt-in local experimental use with `nf4-cuda` reflection on the private Kaggle bridge.** The final check revalidated the immutable bundle, corpus, generated evaluation, 20 delegated agent verdicts and their source evidence. The previously measured integrated and split workflows matched 73/73 and 72/73 cases, respectively, with zero false verified answers. The split injection case still safely abstains. The bound review retains `experimental_unapproved` and Self-RAG defaults to off. [Decision, profile scope and evidence hashes](docs/validation/local-release-2026-11-15.json).

**Clean local application setup passed with verified cached weights.** A clean export of the current working tree passed a fresh locked Python and npm install, frontend typecheck/build, native Compose smoke, host migration and Alembic checks. The complete default-profile suite passed **262 tests with zero skips** after removing the live MPS pin from the test configuration. The real browser workflow uploaded both PDFs, published the index, replayed the baseline answer trace and opened its highlighted original. It verified both comparison sources and exported the exact **INR 100.00** difference. All five cached weight files matched official pinned metadata. Fresh weight downloads remain unverified. The configured registry request returned 403, and anonymous metadata access succeeded.

The selected trained adapter did **not** pass the local `fp16-mps` profile check on this 16 GB MacBook Air. The first load failed the startup decision probe. Loading the runtime before the worker passed the probe and ordinary API admission, but the first document query timed out while generating a candidate. Later requests returned `RUNTIME_BUSY` and the run was stopped. This run does not establish MPS answer accuracy. The earlier split result uses MPS for the separate generator role. Test containers and volumes were removed, and the persistent database remained at `0009_split_reflection`.

## Phase 6 validation — 2026-11-15

**Engineering acceptance passed for the selected experimental v4 checkpoint and tested pairing.** The immutable bundle (`0cfdee37…c2543`) returned the expected outcome in **73/73 integrated PDF-to-answer cases**, with zero false verified answers, zero failed queries and 29 resolvable scoped citations. This includes the original long-tail and arithmetic regressions. [Measured report and source hashes](docs/validation/phase6-validation-2026-11-15.json).

The first complete split run matched 61/73 cases. Its generator omitted currency from otherwise correct amounts. A complete-field instruction passed five separate development calibrations without changing the verifier, weights or serializer. The same-case regression then matched **72/73**, with zero false verified answers or failed queries and 28 resolvable scoped citations. This rerun is not a fresh holdout. The remaining injection case emitted a reserved control string from untrusted source text; runtime validation rejected it and the app safely abstained. Keep that answer-coverage miss for broader adversarial work.

The real target passed **144/144 greedy test episodes** and **324/324 conditional label checks**, including 60/60 raw and constrained initial decisions. An independent NF4 load matched all 192 probability families at the exact recorded HTTP prefixes (maximum absolute difference `1.36e-8`, declared absolute/relative tolerance `1e-3`). Four actual target-bundle pre-load probes rejected a wrong manifest, missing file, invalid token map and missing trained rows. The original bundle remains unchanged.

Twenty cases across totals, dates, currencies, supplied references and addition were inspected and accepted by **Codex as an explicitly delegated agent**, using actual outputs, original pages, matching split renders, retrieval and citations. These are not human verdicts or a transfer of the v3 review. One supplemental split coverage rejection remains recorded. ADR-043 binds the newly collected conditional test scores without repacking the checkpoint; ADR-044 records review authority. The review retains `experimental_unapproved`.

The reviewed service then passed **9/9 ordinary API admission cases**, including both split and integrated strategies, through the real authenticated loopback bridge. Neither admission nor model transport was substituted in this check. Live guards rejected missing authentication, a stale runtime instance, an invalid hash, oversized input and notebook routes. The full suite passed **262 tests with zero skips** and five dependency warnings, including actual OCR/retrieval, Chromium, migrations, the trained CPU pilot, standalone conditional scoring and worker recovery. Ruff, lockfile and whitespace checks passed.

The persistent local database was still at `0003_table_types`. A private custom-format backup passed `pg_restore --list`; forward migrations reached `0009_split_reflection` and Alembic metadata comparison passed. Counts and old-column fingerprints preserved all **50 existing rows across seven original tables**. Object storage and `.env` were not changed, and persistent Self-RAG remains disabled by default. The backup is private under `.data/phase6-validation-2026-11-15/`.

All five identified validation sessions accepted cancellation and subsequently returned gateway HTTP 404; their retired gateway URLs were removed. Both local model ports are closed. The separate creation without a receipt remains recorded with the owned notebook observed off; no duplicate was started from that state. Physical GPU deallocation remains unobservable. MPS cold/memory-pressure failures are retained; calibrate after retrieval loads and keep heavy local model tests separate.

Phase 8 still needs representative grouped data, adversarial development and frozen comparison/ablation evidence. Other generator and reflection profiles require their own quality checks. Do not tune on the used v4 holdout or treat this synthetic agent-reviewed validation as a human research benchmark. Raw outputs, source snapshots and review evidence remain private under `.data/phase6-validation-2026-11-15/`.

## Local-only architecture and independent models — 2026-11-11

Implemented ADR-042. The app, stores, embedding, reranking and verification stay on the device. The fine-tuned role uses a local model service or private Kaggle loopback bridge. Generation independently selects a local service, private Kaggle bridge or external endpoint/key. The workspace exposes answer mode and generation choice. An admitted trained runtime with candidate-scoring support is required for split reflection.

The trained runtime scores the exact external candidate using the existing reflection grammar. Results pin both identities and distinguish split from integrated inference. The provider intent and exact candidate enter the private recovery journal. A real worker exit test replayed confirmed split work without another generator request. Revocation, changed model profile, wrong values and unsupported critics fail explicitly. Existing integrated queries, baseline QA and comparison remain covered.

Migration `0009_split_reflection` broadens the checked runtime scope to pin a generator connection. The disposable database passed upgrade/downgrade/upgrade and Alembic metadata comparison. The existing application database was left at its installed revision. Apply the migration as part of starting the updated local app. [Setup](docs/LOCAL_DEVELOPMENT.md#local-only-model-setup).

**Verification:** 250 full-suite tests passed with zero skips and five dependency warnings. After the final loopback and supplied-token accounting changes, 66 affected boundary tests passed with zero skips. Ruff, lock, whitespace, Markdown file links, Compose configuration, frontend typecheck and build passed. The full suite included actual OCR/retrieval, Chromium, migrations, worker recovery and a real trained CPU pilot. The trained pilot also loaded and scored exact candidates on Apple MPS using its reported FP16 profile.

**Live Kaggle:** the generation bridge returned an authenticated completion from the real SmolLM2 base through local loopback. The fine-tuned bridge loaded the exact Qwen v4 NF4 bundle, emitted Retrieve, rated the correct supplied amount Fully/5 and the wrong supplied amount No/1. Both bridges rejected notebook-administration routes. Kaggle accepted both exact-session cancellations. Physical GPU deallocation remains unconfirmed. [Measured results and source hashes](docs/validation/local-architecture-2026-11-11.json).

At this historical snapshot, the full 73-case workflow and selected-release/pairing review were pending. The November 15 validation above supersedes those gates for the measured profile. Other CPU/MPS target-model capacity and quality remain device-dependent.

## Follow-up on S01–S03 — 2026-11-09

S01 now pairs a distant invoice label and the requested field when both resolve to one pinned paragraph. The model sees two exact excerpts within the original context budget. The field stays inside the retrieved chunk, and citations resolve to the original value. The wrong-invoice and conflicting-table cases still abstain in the integration tests.

S02/S03 get one generation attempt if the model finishes before answering an eligible addition. The trace records the original decision and the policy override, and the app checks the generated integer before returning it. The API regression verifies zero retrieval and no citations, and the control-loop tests reject an incorrect sum. The v4 corpus has 160 training, 40 validation and 60 held-out cases. It excludes both reviewed operand pairs. [ADR-041](DECISIONS.md#adr-041-preserve-invoice-identity-across-distant-fields-and-validate-eligible-addition).

**Local verification:** 239 tests passed with zero skips and five dependency warnings. Ruff, lock and whitespace checks passed. The pinned Qwen tokenizer encoded all 988 v4 episodes within 273 tokens. A 120-step SmolLM2 FP32 pilot trained on v4 and reloaded all 15 token rows. On the addition validation cases it emitted 16/16 initial `[NoRetrieve]` decisions and 1/16 exact candidate answers. That run validates the training pipeline and leaves target model quality open. Private local artifacts are under `.data/phase6-v4-*`.

**Target-model verification:** Kaggle serving setup failed before a Qwen inference step. Kaggle accepted both exact-session cancellation requests. The original private v4 training version stayed queued without a failure reason despite 28.49 GPU hours remaining. The retry used the same training source with a 30-minute execution limit. Kaggle's metadata reported the requested `NvidiaL4X1` shape, while the actual CUDA training and validation artifacts both identified a Tesla T4. Version 2 completed 456 QLoRA steps in 1,013.34 seconds and reloaded all 15 token rows. The downloaded bundle passed all nine file hash checks against the manifest digest emitted by the job. Version 1 still reported queued.

Greedy v4 validation passed 40/40 initial retrieval decisions, 15/16 exact addition answers, 8/8 supplied references, 16/16 supported candidates and 16/16 irrelevant passage checks, with zero false full-support candidates. The verified version 2 checkpoint and validation outputs are private under `.data/phase6-adapter-v4-version2-output/`. The next gate is the 73-case PDF-to-answer workflow, including the original regressions and fresh holdout. Release review remains open.

## Selected adapter human review — 2026-11-09

The reviewer `here-2007` accepted **20/20 formal held-out cases**. The submitted notes cover retrieval policy and the expected field in 12 document questions (four each for total, due date and currency), plus retrieval policy and reference repetition in eight eligible questions. The official recorder and loader validated the record against the immutable checkpoint, frozen corpus and generated evaluation. The retained application report and 15 PDF hashes also matched. Private artifacts are under `.data/human-review-selected-v3/`, including `WORKSHEET.md`, `recorded-review.json` and `supplemental-human-review-amended.json`.

The amended supplementary review has two acceptances and three rejections. S01 confirms an answerable long passage lost the invoice identifier from the candidate's evidence window. S02 and S03 confirm that the eligible arithmetic cases ended at the initial `[Finish]` with no answer. The reviewer amended S04 to accept the abstention on conflicting totals. S05 confirms correct abstention for a missing due date. The original submission remains in the private review history.

The reviewed v3 record remains `experimental_unapproved`. The recorded human notes document the checks listed above. API citation resolution remains evidence from the original disposable workflow run.

## Cleanup, retry, CI and selected-model workflow — 2026-11-09

Audit findings 6–10 are implemented and verified within the operator/local scope. Creation receipts are durable before metadata validation; Start failures enter cleanup; unknown outcomes have explicit operator-confirmed reconciliation without reallocation. Fixed allocation expiry, a detached cancellation watcher and a separate bounded model subprocess cover startup/command loss. Expired status reconciles cleanup before notebook checks. The actual independent watcher requested cancellation, and a later observation confirmed gateway revocation. Physical GPU deallocation remains unverified, and host loss still requires retained-state reconciliation.

The browser retries an unconfirmed submission with the exact original payload/key and freezes editing until receipt or explicit new-query choice. It also clears cached answers/highlights on evidence-recheck errors. CI installs training dependencies, trains a fresh CPU pilot with a 120-step cap, verifies all 15 rows after reload and requires its authenticated runtime test; missing required artifacts fail. The equivalent local train/reload/runtime sequence passed. Bounded invoice paraphrases use `evidence-v1.3`.

The unchanged selected Qwen adapter ran through **32 PDFs and 46 cases with real private GPU inference**: **43 expected outcomes**, zero false verified answers/transport failures and **26 resolvable scoped citations**. Frozen document QA passed 24/24, supplied references 12/12, abstention checks 5/5 and prompt injection 1/1. Long passages passed 1/2; both arithmetic cases safely abstained after initial Finish decisions. The tail-field window retained the amount but lost its distant invoice identifier and was marked irrelevant. All 32 document queries retrieved once; all 14 eligible queries made zero searches. This isolated operator harness injects only experimental admission/private transport, preserves the pilot capability and does not enable production approval. [Measured evidence and exact limits](docs/validation/selected-pdf-workflow-2026-11-09.json).

Final local verification passed **230 tests, zero skips**, five dependency warnings, 130.68 seconds, including real OCR/retrieval, Chromium, worker recovery and the newly trained CPU pilot. Ruff, lockfile, whitespace, frontend typecheck/build and Markdown links passed. Logs/JUnit are `.data/cleanup-retry-ci-final-suite.*`. The live GPU diagnostic separately passed its execution/safety assertions while retaining the three quality failures. Raw model contexts/results and source PDFs are private under `.data/selected-pdf-workflow-2026-11-09*`; citation resolution was checked before dropping the disposable DB. Remote CI has not run. No model weights, used holdout or human approval was changed/fabricated.

## Audit findings 1–5 remediation — 2026-11-09

Findings 1–5 are implemented and verified with regressions. Shared QA verifier `evidence-v1.2` rejects unresolved table fields for baseline and Self-RAG; older supported result/event/citation reads are rechecked. Exact Qwen tokenizer/template admission, complete serialized budgets and explicit contiguous field windows preserve source offsets and the original 512-token manifest limit. The app rejects inconsistent input counts and avoids dispatching contexts that cannot fit. The review packet includes actual generated outcomes, and the runtime consumes and revalidates the recorded-review schema. Kaggle Stop uses the private creation/session binding and configured account independently of notebook edits or deletion. [Detailed closure](docs/validation/project-audit-2026-11-09.md#findings-15-remediation--2026-11-09) and ADR-035–037 record scope and tradeoffs.

Final verification passed **217 tests, zero skips**, five dependency warnings, 123.90 seconds. OCR, real retrieval models, browser flows, worker recovery and the trained CPU pilot were enabled. Ruff, lockfile, whitespace, frontend typecheck and production build passed. The tokenizer cache flag passed with a real CPU embedding/reranking probe, and the actual selected bundle's saved tokenizer matched the cached app tokenizer. Logs and JUnit are `.data/audit-fixes-final-suite.*`. Two child-process recovery fixtures initially lacked their synthetic tokenizer setup; final caller review also found a poll-before-cancel dependency in the operator Stop helper. Both were corrected, including eight operator Stop cases, before the complete final rerun.

The unchanged selected adapter's actual test outputs now export to `.data/audit-fixes-selected-review-packet.json`: 36 cases, 96 evaluated episodes, 60 explicitly unrun diagnostic contexts. No human review was created. App/runtime must upgrade together for the required template hash and provision the cached tokenizer with `scripts/setup_search.py --reflection-tokenizer`. Baseline setup remains independent. No new target GPU run, live Kaggle cancellation or remote CI execution was performed. At that checkpoint findings 6–10 remained open; the follow-up above records their implementation. Release gates remain open.

## Repository audit — 2026-11-09

The audit found ten discrepancies across evidence verification, generation context budgets, manual review artifacts, Kaggle cleanup, application evaluation coverage, frontend retries and CI. Application code was unchanged. Targeted reproducers returned a supported paragraph total despite a competing table total, a generation refusal for a valid 239-token retrieval passage, and rejection of the review CLI's recorded output by the runtime schema. Half of the v3 required questions fall outside the current verifier grammar. [Ranked findings and next steps](docs/validation/project-audit-2026-11-09.md).

The audit reran the full local suite with OCR, real retrieval, Chromium, worker recovery and the trained CPU pilot enabled. **185 tests passed** with zero skips and five dependency warnings in 128.60 seconds. Frontend typechecking/build, Ruff, lock and whitespace checks passed. The selected v3 adapter's nine-file integrity and synthetic results were verified. Fresh remote GPU, remote CI, hosted multi-user and capacity checks were outside this audit.

## Phase 6K private serving and adapter v3 — 2026-11-09

- Started a private owned Kaggle Jupyter GPU session, uploaded the checked Qwen pilot bundle and allowlisted source, and called the actual model over its authenticated HTTPS gateway from the local machine. Required retrieval, eligible skip and passage-conditioned candidate steps completed. Missing runtime credentials returned 401. Replaying a step returned the same result. [Live evidence](docs/validation/phase6k-private-serving-2026-11-09.json).
- The controller started another unique private session and repeated Start with the same state returned the same session ID. Kaggle accepted cancellation for both exact sessions. The original serving gateway later returned HTTP 404 and its retired URL was removed from local scratch state. The separate controller canary remains at `stop_requested`. GPU deallocation remains unconfirmed through the available API.
- Added an operator CLI, private atomic session state, source/owner/version checks, bounded SDK transport, duplicate-create protection, exclusive probe execution and separately retryable cleanup observations. The application feature flag stays gated. [Runbook](docs/LOCAL_DEVELOPMENT.md#phase-6k-private-kaggle-serving-probe).
- The trainer masks unsupported answer words while keeping critic supervision. The immutable v3 corpus has 96 training, 24 validation and 36 independent held-out cases with opaque anchors and paired changed values. The Qwen NF4 run completed 312 optimizer steps on one T4 and reloaded all 15 rows. [ADR-034](DECISIONS.md#adr-034-mask-unsupported-answer-words-and-test-copied-values-with-opaque-evidence).
- On 96 matched greedy episodes from the v3 holdout, exact supported values improved from 7/24 to 24/24, required retrieval from 20/24 to 24/24, and irrelevant passage rejection from 21/24 to 24/24. Both adapters skipped 12/12 eligible queries. False full-support claims fell from 12/48 to 0/48 evidence candidates. The selected weights match the validation-only bundle. All nine file hashes and the 36-case review packet passed verification. These are synthetic model-step results in gold contexts. [Measured comparison](docs/validation/phase6-adapter-v3-2026-11-09.json).
- The packaged operator command started a private GPU session and served the exact selected v3 bundle over HTTPS. All three probe steps completed, missing runtime credentials returned 401, and replay matched the original responses. The legacy prompt `What is 1 plus one?` returned `[Finish]`. That prompt is outside the application's digit-only addition pattern, and the v3 eligible holdout covers supplied-reference repetition. Kaggle accepted cancellation for the session. [Live evidence](docs/validation/phase6k-private-serving-2026-11-09.json).
- **185 tests passed** with zero skips and five dependency warnings in 125.32 seconds after the resumed operator probe change. OCR, real retrieval, Chromium, worker recovery and the trained CPU pilot were enabled. Ruff, lockfile and whitespace checks passed.

## Phase 6 recovery and target model replacement — 2026-11-09

- A real worker process exited after confirming a candidate and after receiving an unconfirmed response in separate tests. The dispatcher resumed the confirmed run without repeating either the completed model calls or the original scoped search. The new worker used the original deadline, token budget and trace prefix. The unconfirmed run failed once and both paths erased the private recovery journal.
- The direct-versus-service parity probe passed with the real SmolLM2 bundle locally and the Qwen NF4 bundle on Kaggle over authenticated loopback. Both initial decision probes and an evidence candidate matched their direct counterparts. Qwen returned `[NoRetrieve]` on the document probe and `[IsSUP:No]` on the candidate. Transport parity is independent of model correctness. [Measured run](docs/validation/phase6-qwen-2026-11-09.json).
- The user authorized replacing the gated Llama base. Qwen3-4B-Instruct-2507 is pinned at `cdbee75f17c01a7cc42f958dc650907174af0554`. An unauthenticated weight range returned 206. The actual Qwen tokenizer round-tripped all 15 reflection tokens and encoded 372 episodes within 221 tokens. The expanded frozen synthetic corpus contains 54 training, 16 validation and 24 held-out cases. [ADR-031](DECISIONS.md#adr-031-pin-open-qwen3-4b-instruct-2507-for-the-trained-release).
- Kaggle trained the Qwen NF4 adapter for 214 optimizer steps in 216.0 seconds on one Tesla T4, with 6.40 GiB peak allocated GPU memory. All 15 token rows changed and reloaded identically. The raw decision was correct on 12/12 safe-to-skip and 0/12 retrieval-required held-out cases. The review packet is generated for diagnosis. Human acceptance and the approved release remain open. [Measured run](docs/validation/phase6-qwen-2026-11-09.json).
- A lower-rate three-epoch trial ran for 321 optimizer steps on one Tesla T4. It passed 16/16 raw initial validation decisions. The exact saved weights then passed 24/24 raw initial decisions on the same frozen holdout used by the first trial. Greedy runtime decoding matched 34/47 candidate episodes in gold contexts. Seven of 13 candidates expected to be fully supported differed from the target text while still receiving the full-support label. Both models remain experimental. [Measured runs](docs/validation/phase6-qwen-2026-11-09.json).
- **143 tests passed** with zero skips and five dependency warnings in 114.15 seconds, including Chromium, PostgreSQL migration through `0008_reflection_recovery`, the real scoped retriever, actual worker exits and the real pilot transport. [Runtime runbook](docs/LOCAL_DEVELOPMENT.md#phase-6-private-reflection-runtime).

## Phase 5 recheck and Phase 6 runtime — 2026-11-09

- Reproduced and fixed duplicate segment-and-evidence commits in the Phase 5 trajectory validator. The frozen corpus and serializer remain unchanged. An eight-step CPU train and reload passed after sharing base initialization with the model service. Hugging Face still returned 403 for the pinned Llama tokenizer and weight index. Kaggle returned 403 for its tokenizer. Target training and human release review remain open.
- The private FastAPI service loaded the trained SmolLM2 pilot and returned `[Retrieve]` over authenticated loopback HTTP with probabilities matching direct inference. A passage-conditioned candidate returned `[IsREL:No]`. Tests rejected pilot admission, missing token rows and a changed manifest. Replay, changed request hashes, instance changes and cancellation were exercised with a scripted decoder.
- The workspace integration used the actual published corpus and retrieval models with a scripted protocol responder. It forced retrieval on a document query, opened a verified citation, skipped retrieval for an eligible general query, and blocked the next model step when a source was deleted during the decision. Migration `0007_reflection` pins the admitted runtime and preserves existing baseline runs.
- **140 tests passed** with zero skips and five dependency warnings in 104.06 seconds. The suite included OCR, real retrieval, Chromium, the database migration round trip and loopback pilot inference. Ruff, lockfile and Markdown checks passed. The backend Docker image built. Local artifacts are under ignored `.data/phase6-*`.
- App Self-RAG requires an approved target bundle and explicit runtime configuration. The approved remote deployment and worker recovery from confirmed history are pending. The app currently fails closed after worker lease loss following model dispatch. [Runtime runbook](docs/LOCAL_DEVELOPMENT.md#phase-6-private-reflection-runtime).

## Phase 5 release audit and hardening — 2026-11-09

- Phase 4 could advertise a stale or corrupted CSV as ready while rejecting its bytes. Two failing PostgreSQL regressions reproduced the discrepancy. Report creation and replay now verify the finding, and ready metadata verifies the same bytes and checksum as download.
- Phase 5 accepted fabricated committed segments, unsupported evidence and passages introduced during Continue. The loader now checks candidate-to-commit provenance and allows empty required retrieval to finish without a claim. The frozen pilot bytes and serializer are unchanged.
- A fresh 120-step CPU run reloaded all 15 reflection rows and generated `[Retrieve]`. It scored 24/24 raw initial decisions on synthetic held-out cases and exported a 24-case review packet bound to the corpus and artifact hashes. A separate 8-step run confirmed validation-only defaults. The packet contains predictions for review and has no human submission. Local run artifacts are in ignored `.data/phase5-recheck-*` and `.data/phase5-review-packet.json`. [Training runbook](docs/LOCAL_DEVELOPMENT.md#phase-5-offline-reflection-training).
- **134 tests passed** with zero skips and five dependency warnings in 103.79 seconds. Ruff, lockfile, whitespace and changed Markdown link checks passed. The application rejects enabling the Phase 6 runtime and `self_rag` requests fail with `MODEL_NOT_READY`. The pinned Hugging Face tokenizer and weight index and Kaggle tokenizer still returned 403. The target checkpoint and manual release gates remain open.

## Phase 4 re-audit and Phase 5 pilot — 2026-11-09

- Reproduced a verified INR 100.00 difference despite a conflicting total in a reviewed table. New comparisons abstain when a table contains a comparison field. Previously verified results and CSV bytes require a recheck. The PostgreSQL regression covers both paths with a real reviewed PDF.
- **129 tests passed** with zero skips and five PyMuPDF/SWIG warnings in 103.52 seconds. The suite exercised actual OCR, retrieval and the pinned small-model backend test, Chromium, and the disposable migration round trip. TypeScript, Next.js production build, backend Docker build, Ruff, environment lock and whitespace checks passed.
- Phase 5 registers 15 reflection tokens, builds a checksummed corpus, masks context and padding, trains PEFT adapter/token rows, verifies reload and greedy decoding, and saves held-out label confusion matrices. The synthetic pilot has 12 train, 6 validation and 24 test samples. [Training runbook](docs/LOCAL_DEVELOPMENT.md#phase-5-offline-reflection-training).
- Real SmolLM2 pilots completed 120 optimizer steps on CPU FP32 and Kaggle Tesla T4 NF4. Both changed and reloaded all 15 token rows and generated `[Retrieve]` on the saved training probe. Raw held-out initial decisions were 24/24 on CPU and 17/24 on T4. T4 missed seven retrieval-required decisions. Relevance was 25/66 on CPU and 38/66 on T4. The first GPU version exposed an embedding dtype mismatch on reload. Matching training and reload preparation resolved it. All nine files in each completed bundle matched their hashes. [Measured pilot results](docs/validation/phase5-pilot-2026-11-09.json).
- Configured Hugging Face and Kaggle model access both returned HTTP 403 for the Meta Llama 3.2 3B Instruct tokenizer. The larger adjudicated corpus, target checkpoint and 20 manual release probes remain open. No Self-RAG application capability is enabled.

## Phase 3 repairs and Phase 4 verification — 2026-11-09

- Fixed trace replay after terminal disconnection, confirmation tied to an exact preview version, dispatcher timeouts without worker delivery, upload retry keys, endpoint capability and labeling, and stale content after source deletion. Both event API issues now have regression coverage. [Audit and resolution](docs/validation/phase3-review-2026-11-09.md).
- The isolated browser compared two unpublished PDFs and showed an exact **INR 100.00** difference, opened both page highlights and downloaded the matching CSV. A subsequent refresh after source deletion cleared the finding and highlight, and the report route returned 410. PostgreSQL tests verified that a later correction leaves a pinned investigation intact. Integrity checks rejected a tampered raw span, altered typed values and changed report bytes.
- **118 Python tests passed** with zero skips and five PyMuPDF/SWIG warnings in 101.05 seconds. The suite included actual OCR, EmbeddingGemma 2, MiniLM and SmolLM2 checks, the synthetic development evaluation, three Playwright scenarios, and the migration round trip through `0006_comparisons`. Ruff, lockfile consistency, TypeScript, Next.js production build and the default backend Docker build passed. Local logs and JUnit output are under ignored `.data/phase4-*`.
- Phase 4 uses bounded labeled paragraph fields, exact linkage, Decimal arithmetic and scoped SQL. Public deployment, target 2-vCPU performance, external HTTPS inference and the grouped holdout remain unverified. The existing application database was outside the migration test. [Runbook](docs/LOCAL_DEVELOPMENT.md#phase-4-deterministic-comparison).

## Historical Phase 3 correctness audit — 2026-11-09

The current suite passed 97 tests again with zero skips. Additional Chromium and API probes reproduced lost trace events after a run finishes during disconnection, review confirmation carrying over to a different extraction, expired queued runs that never terminate without a worker, upload retry keys that can duplicate accepted work, stale or misleading endpoint state, and revoked content remaining visible after workspace refresh. Event negotiation and expired JSON result links also need correction. [Findings and reproductions](docs/validation/phase3-review-2026-11-09.md).

## Phase 2 repairs and Phase 3 verification — 2026-11-08

The full suite passed **97 tests**, zero skips and five third-party PyMuPDF/SWIG warnings in 88.61 seconds. The run enabled actual OCR, EmbeddingGemma 2, MiniLM and SmolLM2 checks, the 50-question development evaluation, real Celery delivery and Chromium. Ruff, lockfile consistency and Git whitespace checks pass. The TypeScript check, Next.js production build and default backend Docker build pass. The npm audit reported zero production dependency vulnerabilities.

All six Phase 2 findings now have permanent regressions. Dispatch authorization spans the bounded HTTP request write. Site-local IPv6 is rejected. The verifier abstains on ambiguous invoice identities and competing unparsed field values. Migration 0005 backfills a last-published pointer so replacement snapshots preserve eligible surviving versions. Readiness shares artifact validation with retrieval. The runbook question is accepted by the explicit grammar.

The Next.js workspace implements upload progress, extraction/search state, document selection, page previews, review confirmation, cited answers, traces and endpoint management. A real browser test uploads a two-page PDF, publishes it with real models, asks through a synthetic endpoint, reconnects with a duplicated event, opens and highlights page 2, cancels a run, replaces and revokes a credential, and checks a corrupt-file error and a 390-pixel layout. The local API token stays in server configuration. The user-entered endpoint key passes through the setup form. The real model test is a separate backend check.

Live SSE checks cover heartbeats, disconnect without cancellation, source revocation, expired/future/conflicting cursors and terminal replay. A real Celery worker is killed after retrieval starts. A restarted worker recovers its expired lease and completes one terminal answer. Desktop and mobile screenshots were inspected locally.

The CI workflow now provisions the frontend and all model/browser checks. Its syntax and flags were checked locally, and this revision's remote CI has not run. Existing application containers and their database were left in place. Source hashes and environment details are recorded in [the validation artifact](docs/validation/phase3-2026-11-08.json).

## Historical Phase 2 correctness audit — 2026-11-08

The reviewed implementation passed 85 tests while additional probes found six issues across seven failing cases. These covered first-write authorization, site-local IPv6, ambiguous evidence, surviving source versions, readiness and the runbook example. The fixes are recorded above. [Original findings and reproductions](docs/validation/phase2-review-2026-11-08.md).

## Phase 1B fixes and Phase 2 verification — 2026-11-08

The six Phase 1B fixes preserve both outside-table text runs and rotated review order, promote a reused completed version atomically, replay accepted uploads during OCR outages, extract complete one-row/column raster grids, and assign each OCR line at most once. Parser and manual review identities were bumped to preserve earlier promoted sources.

Phase 2 adds workspace-scoped Qdrant and persisted BM25 snapshots, atomic publication, pinned CPU EmbeddingGemma 2 and MiniLM reranking, evidence-v1 field answers, exact raw-span citations and durable queries with cancellation and JSON events. Custom endpoint create/edit/validation/revocation use scoped immutable versions, encrypted credentials, and DNS-pinned outbound connections. Provider output must match the verified answer. Publication and query jobs use the existing Redis/Celery delivery and PostgreSQL leases.

- **Full local suite:** 85 passed, zero skips and five third-party PyMuPDF/SWIG warnings after a locked sync with OCR and retrieval extras. It exercised actual PaddleOCR, embedding and reranker weights, SmolLM2 on a loopback HTTP completion server, and real Celery delivery. It also covered concurrent publication, endpoint version replay, cancellation, revocation and deletion during inference. With only the OCR test flag, 74 passed and 11 Phase 2 model checks skipped as expected.
- **Build and schema:** Ruff, lockfile and Git whitespace checks passed. The default API image built. The disposable PostgreSQL migration test ran upgrade/downgrade/upgrade and Alembic schema comparison. Existing application containers and their database were left in place.
- **Development metrics:** Expected evidence ranked first for all 50 questions and all five negative cases abstained on synthetic invoices. Median retrieval was about 77 ms and Python peak RSS about 2.08 GB on the development Mac. These are fixture results. The separate grouped holdout remains frozen and unrun. [Evaluation with rankings and model identities](docs/validation/phase2-development-2026-11-08.json).

## Historical Phase 1B correctness audit — 2026-11-08

The reviewed commit failed six additional checks. [Full findings and reproduction details](docs/validation/phase1b-review-2026-11-08.md). The 47-test suite passed at that commit with real OCR, zero skips and five dependency warnings (18.98 seconds). Ruff, Alembic and the real Docker/Celery preview/review/deletion smoke passed. [GitHub CI for the reviewed commit](https://github.com/here-2007/DocuMind/actions/runs/42480651033) passed 47 tests in 30.85 seconds.

Additional checks beyond that suite confirmed:

1. F1: words on both sides of a native table can disappear through repeated bounding-box filtering.
2. F2: no-edit approval can reorder an already-rotated page's text.
3. F3: reusing a completed review configuration with a new request key can leave latest and preview pointing to different versions indefinitely.
4. F4: accepted uploads cannot replay during an OCR artifact outage because model validation precedes the request ledger lookup.
5. F5: complete single-row/column raster grids are ignored by a hardcoded 2×2 minimum.
6. F6: an OCR-style box centered on a shared divider is duplicated into adjacent cells. This was an adapter-input boundary check, not a box observed from live OCR.

Three fresh-turn-context reviewers inspected disjoint scopes using source and executable probes. During that audit the application source, tests, configuration and migrations matched all 34 recorded build hashes. The findings above now have permanent regressions.

## Earlier Phase 1B build verification — 2026-11-08

The full local suite passed **47 tests**, with zero skips and five PyMuPDF/SWIG dependency warnings, using the locked OCR extra and actual downloaded/hashed model artifacts. The three real OCR tests skip explicitly unless DOCUMIND_TEST_OCR_ROOT is set. Ruff passes. Final Docker rebuild and upgrade to 0003_table_types passed; Alembic reports no new operations. The expanded real HTTP/Celery smoke passed native text/table preview → correction/review → pinned old-version read → deletion. Compose request logging was observed with its returned request ID. Both smoke fixtures (including one interrupted by container recreation) were verified synthetic and cleaned. [Runtime environment, checks and source hashes](docs/validation/phase1b-2026-11-08.json) preserve this evidence.

Implemented/covered: both text-read deletion races; full element pagination; emitted redacted request records under real Uvicorn; native tables across all four PDF rotations; empty cells/merged native headers/adjacent text; actual OCR of clean scans, scanned table, mixed/rotated PDF and API/job/review flow; manual correction with old-version resolution; invalid parent/null/array table provenance; missing OCR models; legacy reprocess fingerprints; consistent lease/deadline clock checks.

A read-only independent review found table-adjacent text loss, merged-span errors and legacy idempotency drift during implementation; these were fixed and independently checked. It also found PostgreSQL's NULL CHECK loophole for array coordinates; the object/type guard and regression now pass. Its final bounded recheck passed six targeted cases with no remaining actionable blockers in that scope. The suite exposed a host/DB clock mismatch; recovery, heartbeat and queued writes now use the same application clock, with an expired-heartbeat regression. These are correctness discoveries, not benchmark improvements.

Actual OCR diagnostics: [two clean synthetic scan metrics](docs/validation/phase1b-ocr-2026-11-08.json) record recognized text, whitespace-normalized CER/WER, elapsed time, actual environment and model revisions/file hashes. Both tiny cases had zero observed CER/WER; this is not representative accuracy or confidence calibration. First initialization took about 61 seconds; subsequent recorded cases were about 2.6/1.6 seconds on the development Mac, not the proposed host.

The optional OCR Compose overlay passes configuration/volume/platform merge checks only; its runtime was not executed. A package-download timeout during the native rebuild was resolved by increasing the download timeout and caching dependencies. Fresh copied-source startup was not repeated in this task.

## Historical correctness review — 2026-11-08

[Original findings and reproductions](docs/validation/phase01-review-2026-11-08.md). F1–F3 are resolved with permanent tests in test_review_regressions.py: deletion after initial access checks, element cursor identity and effective request logging. The original 28-test suite passed while missing these defects; retain the review as a lesson, not an open blocker.

## Initial build verification — 2026-11-08

Evidence: [environment, package versions and source/config hashes](docs/validation/phase01-2026-11-08.json).

| Check | Observed result |
|---|---|
| `uv run pytest -q` | 28 passed, 0 failed, 6 third-party deprecation warnings; macOS arm64 / Python 3.12.13 |
| `uv run ruff check backend scripts migrations` | Passed |
| `uv run alembic check` | No new upgrade operations |
| Migration upgrade/downgrade/upgrade | Passed in a disposable PostgreSQL database |
| Full Docker API/dispatcher/worker smoke | Passed upload → real Redis/Celery → PostgreSQL → text/PNG/original reads → revocation/cleanup; synthetic fixture deleted |
| Clean copied-source startup | Passed with new private config, empty DB/volumes and ephemeral ports; disposable resources removed |
| Remote CI | Workflow added; execution not observed |

Coverage includes rotated/image-only/encrypted/corrupt PDFs and limits, workspace isolation and scoped references, concurrent duplicate requests, immutable promoted sources, retry/lease recovery after a killed process, stale workers, reprocessing/preview races, deletion and orphan collection. This is native-slice coverage; it does not establish full-product acceptance scenarios or model behavior. Current process/service status must be checked when needed; the earlier running containers are not a permanent fact.

## Pending gates and limitations

- General layout/Docling, unruled/ragged/merged raster tables, automatic multi-page table linking and additional upload formats remain unimplemented. Existing text/header review cannot add missing cells or edit geometry.
- Local single-workspace Bearer access is not public/multi-user authentication. Public deployment, load/security evaluation and hosted storage remain unverified.
- Source storage is the private filesystem adapter (ADR-022), not a tested S3 service. Host and Compose object roots must not be mixed against the same populated database.
- English CPU OCR and bounded ruled-table extraction are available and require explicit human review. Confidence remains uncalibrated, and OCR/table quality beyond clean synthetic fixtures is unverified. The Next.js viewer supports page inspection and whole-document confirmation. Geometry correction remains outside its scope.
- A real external HTTPS provider, the proposed 2-vCPU host, representative retrieval quality and the optional OCR Docker runtime remain unverified. Remote CI evidence applies to the earlier Phase 1B commit.
- A trained Self-RAG release and managed Kaggle remain later milestones. The Phase 2 custom endpoint supports baseline QA. The evidence gate currently handles explicit fields in paragraph text on a resolved source. It abstains on unsupported questions.

## Next steps

Improve long-window identity/evidence handling and eligible arithmetic coverage using fresh grouped development data. Re-evaluate on a fresh holdout without tuning to the used v3/application results, then complete genuine human review and approved deployment checks. Finish hosted per-owner Kaggle credential/connection lifecycle and resource-release verification. Run the changed CI after publication. Phase 7 remains independent. [Audit execution order](docs/validation/project-audit-2026-11-09.md#recommended-execution-order).

For startup/testing commands use [LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md). Initial configuration runs once and refuses to overwrite `.env`; do not reset volumes or rerun setup destructively to gather context.

## Condensed history

- **2026-11-07 — design audit:** revised token budgets, extraction/index readiness, durable dispatch/publication, evidence checks, scoped provenance, reflection protocol, query replay and independent release gates (ADRs 011–019). Documentation checks were completed; they did not implement services.
- **2026-11-08 — model/inference decisions:** selected EmbeddingGemma 2 / hybrid-v3 (ADR-020), then managed Kaggle/custom endpoints and immutable trained releases (ADR-021). Runtime gates remain pending.
- **2026-11-08 — native build:** implemented and locally verified Phase 0/1A (ADR-022). Durable debugging lessons are in MEMORY; the evidence artifact above preserves package versions and hashes. No Git clone/commit was claimed.
- **2026-11-08 — persistent context workflow:** established root AGENTS/MEMORY/PROGRESS/DECISIONS ownership under ADR-023. Previous status/decision paths are compatibility pointers. This was documentation maintenance, not a new application milestone or runtime test run.

## Historical context-maintenance verification — 2026-11-08

Verified the context links, Markdown structure, original ADR IDs, referenced source/test names and canonical status/decision ownership. The recorded native implementation artifact hashes still match the current files. No application code/configuration, credentials, model assets or validation artifact was changed, and no runtime tests were rerun for this documentation-only task. Old status narration was condensed; substantive decisions and debugging lessons were retained in their owners.

## Source-control publication — 2026-11-08

At the user's request, initialized Git on `main` and configured public remote [here-2007/DocuMind](https://github.com/here-2007/DocuMind). The remote was empty when checked; no existing history was replaced. The snapshot includes implementation, tests, migrations, architecture/contracts, persistent context, synthetic validation records and the owner-supplied reference PDF. Private configuration, local documents/derived assets, dependencies, caches and OCR model weights remain excluded.

Publication preparation verified that all 34 source/configuration hashes still match the recorded Phase 1B validation, so no application tests were rerun for this Git/documentation task. Initial implementation commit `82d4c327919b60593b1ce72a6c387b9b1609af97` was pushed successfully to `origin/main`; `git ls-remote` confirmed the remote branch matched that commit. Local main tracks origin/main. GitHub Actions was triggered by the push; its completed success was subsequently observed during the correctness audit above. The publication record was committed separately from the initial implementation snapshot.
