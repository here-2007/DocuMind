# DocuMind deep audit — 2026-11-16

DocuMind ingests local PDFs, publishes workspace search snapshots, serves checked answers and exact invoice/PO comparisons, and runs experimental trained reflection. This audit assumes one local operator, the documented host/Compose setup and optional private model services. It covers application entry points, ingestion/storage, jobs, retrieval, evidence/money, model protocols, Kaggle lifecycle, browser recovery, evaluation, configuration, dependencies and release state.

The review found **15 actionable findings** spanning reproduced defects, security hardening and unresolved operations/release work. The normal suite passed **297 tests with zero skips**. The extra audit probes exposed failures outside that suite. Priority P1 means address first, P2 means material correctness or recovery work. Implementation code is unchanged by this audit.

**Subsequent remediation:** findings **1–7** were fixed and verified after this audit. The final remediation suite passed **310 tests with zero skips**. Findings **8–15 remain open**. The original observations and evidence below are retained as the pre-fix snapshot. [Remediation record](audit-fixes-1-7-2026-11-16.json) · [Current progress](../../PROGRESS.md).

## Must fix

### 1. P1 — Qdrant bypasses the application access boundary

**Observed.** The documented Compose service publishes an unauthenticated Qdrant port. Requests from `https://audit.invalid` received an allowing CORS header. A disposable collection was created, read and deleted without credentials, and the preflight allowed DELETE. Its cleanup returned 200. A local process, or a browser permitted to reach the local network, can access the index directly. Browser local-network permission policies can affect whether an external website reaches the port.

**Impact and fix.** This exposes vector/index metadata and permits index destruction or modification outside workspace authorization. Require a private Qdrant key in both the container and client, disable broad CORS and keep the loopback binding. [Compose service](../../compose.yaml#L26) · [Index transport](../../backend/documind/search_index.py#L81).

### 2. P1 — A cancelled worker can recreate a page after deletion succeeds

**Reproduced.** Pause ingestion after its heartbeat and before `objects.put`. Complete the deletion job, then let that write proceed. Promotion correctly fails its final visibility check, but the PNG now exists under the deleted document. The cleanup receipt still says `succeeded` and quota is released. The ordinary deletion test writes staging artifacts before cleanup, so it misses this ordering.

**Impact and fix.** Sensitive page bytes can remain after successful physical cleanup until orphan collection. Coordinate staging writes and cleanup through the same document lock or deletion barrier. Preserve the final DB fence and cover the heartbeat/write/deletion race. [Staging write](../../backend/documind/repository.py#L718) · [Physical cleanup](../../backend/documind/repository.py#L1006).

### 3. P2 — A timed-out reflection step keeps occupying the runtime

**Reproduced.** A synthetic real HTTP runtime waited inside its decoder while the app's one-second transport budget expired. The query became failed with `INFERENCE_TIMEOUT`, but the runtime cancellation event stayed unset. Cancelling the already-failed query did not signal it either. In production the request carries the query deadline, while the HTTP call is capped at 30 seconds.

**Impact and fix.** The user sees a completed failure while the model continues consuming resources and can reject later work as busy. Bound a step by the active call deadline and send best-effort cancellation on transport failure before discarding its identity. Retain the query-wide budget for later steps. The earlier trained MPS timeout remains separate hardware evidence. [Step deadline](../../backend/documind/reflection_queries.py#L173) · [Call timeout](../../backend/documind/reflection_queries.py#L230) · [Terminal cancel handling](../../backend/documind/queries.py#L515).

### 4. P2 — The model guardian leaves descendants alive when the parent exits early

**Reproduced.** A guarded parent started a harmless sleeping descendant and exited normally. `bounded()` returned zero. The descendant remained alive after the original expiry. The audit killed that synthetic process afterward.

**Impact and fix.** Startup helpers or model descendants can outlive the stated process-group lifetime when their direct parent exits first. Clean up the owned process group in the guardian's `finally` path, including normal and early-error exit. Existing tests cover timeout with a live parent. [Guardian](../../backend/documind/runtime_lifetime.py#L10).

### 5. P2 — Readiness is green against an incompatible database revision

**Reproduced.** On a disposable database at `0009_split_reflection`, `/ready` returned 200 with native ingestion enabled. Upload then returned 500 because the code needs the `0010_artifacts_dispatch` columns. The persistent installation is also still at 0009, although this audit left it untouched.

**Impact and fix.** A host startup can look healthy before its main operations fail. Check the installed Alembic revision against the application head at startup/readiness and return an actionable migration error. The setup runbook already requires the upgrade. [Readiness](../../backend/documind/api.py#L154) · [Required migration](../../migrations/versions/0010_artifacts_dispatch.py).

### 6. P2 — Cleanup can wait for an index lock beyond its worker lease

**Reproduced.** Hold a publication's advisory lock and start deletion cleanup with a three-second lease. Cleanup remained blocked after the lease expired. Once the lock was released, its first heartbeat failed with `LEASE_LOST`. The blocking acquisition has no timeout and renews no heartbeat while waiting.

**Impact and fix.** A suspended or stuck publication holder can occupy a cleanup worker indefinitely while dispatchers expire its lease. Use bounded/nonblocking acquisition and the existing retryable `PUBLICATION_BUSY` path, or renew the lease during a bounded wait. [Blocking lock](../../backend/documind/publications.py#L144) · [Cleanup caller](../../backend/documind/publications.py#L494).

### 7. P2 — Losing a saved connection receipt creates duplicate connections

**Reproduced in Chromium.** Drop the response after a connection create succeeds, then retry the unchanged form. The browser submitted a different request key, and two connection records were accepted. The first attempt also cleared the credential field, requiring re-entry. This probe used a synthetic local API boundary to isolate the frontend behavior.

**Impact and fix.** Users get duplicate saved destinations and cannot recover the accepted create/edit reliably. Retain the immutable pending payload and request key through an unknown outcome, with the credential held only in transient memory. Recover the accepted connection before starting another create. [Connection form](../../frontend/components/connections.tsx#L33).

### 8. P2 — A “complete” Phase 8 report can omit model arms

**Reproduced.** In a throwaway copy of the saved results, change one row's `arms` to `{}`. Merge it with the other valid shards. The report exited zero and announced 200/200 checked cases, while each QA arm contained only 199 outcomes. The condition validates the arm set only when the dictionary is nonempty.

**Impact and fix.** Missing inference rows can pass the final completeness gate. Record an explicit retrieval-only versus paired run kind and require every paired case to contain every declared arm, including explicit failed outcomes. [Arm check](../../scripts/phase8_report.py#L114) · [Success exit](../../scripts/phase8_report.py#L367).

### 9. P2 — Report admission accepts missing runtime evidence and invalid code hashes

**Reproduced.** The reader accepted a copied model run after deleting `environment.json`. It also accepted a copy with all nine recorded code hashes replaced by zero digests. The optional profile check compares profiles that exist, and recorded implementation hashes are never verified or reconciled across shards.

**Impact and fix.** A shard from another implementation can enter a supposedly controlled comparison without its model evidence. Require model-run environment/capability records, validate their identities and define a frozen code identity for the comparison. Reject incompatible shards or report them as separate experiments. Retain matching source snapshots for replay. [Optional profile admission](../../scripts/phase8_report.py#L93) · [Recorded source hashes](../../scripts/evaluate_phase8.py#L242).

### 10. P2 — Forty retrieval-ablation questions lose the scope needed to define their gold source

**Source and saved-manifest evidence.** All 40 `secondary_field` questions omit an invoice identifier. For example, P8-001-2 asks “What is the balance due?” with one selected source in the case. The ablation searches all 80 documents, then scores only that selected source as relevant. Forty of the 120 retrieval rows therefore omit part of the question's input and have an arbitrary gold document among many plausible sources.

**Impact and fix.** The aggregate retrieval figures mix unambiguous searches with queries whose intended source is unavailable to the retriever. The published scope distinction is accurate, but it does not repair the gold-label ambiguity. Honor the frozen case scope for these queries. A separate global benchmark should include sufficient identity in the question and be frozen as a new version. Preserve the old results as historical. [Global ablation search](../../scripts/evaluate_phase8.py#L136) · [Dataset](../../evaluation/datasets/phase8-v1.jsonl).

### 11. P2 — The trained “raw candidate” column discards rejected generated text

**Reproduced from saved outputs.** In 64/200 broader cases, the runtime generated nonempty candidate text, while the arm's `candidate` field is empty. P8-001-3 generated `EUR`, and P8-012-5 generated `51`, before the loop rejected them. The field is populated from committed segments after filtering. The wire records preserve the actual outputs.

**Impact and fix.** Raw model behavior and protected application behavior are conflated in the summary and exported answer table, weakening failure diagnosis. Save generated candidates and their rejection reasons separately from committed text. Define a raw-output scoring rule for multiple candidates and recompute that metric from the saved wire evidence. The existing final-answer accuracy totals are unaffected by this column problem. [Post-filter assignment](../../scripts/evaluate_phase8.py#L460) · [Raw score](../../scripts/evaluate_phase8.py#L482).

## Should fix and unresolved release work

### 12. P2 — Host PDF parsing inherits operator environment and host privileges

**Verified configuration.** The parser subprocess inherited a synthetic parent environment marker. The launcher supplies no restricted environment or per-process memory policy. It shares the worker's user, filesystem and network access. Compose bounds worker memory, while the documented host path has no equivalent isolation. Text extraction starts before rendered-pixel accounting can bound its work.

**Impact and fix.** A parser crash is isolated, but a decompression/memory attack can affect the host. A future native parser compromise would inherit the operator's access. Use an environment allowlist and a constrained parser process/container with only required inputs and output access. This audit demonstrated inheritance and missing isolation. It did not demonstrate code execution from a PDF. [Parser launcher](../../backend/documind/parser.py#L306) · [Container limits](../../compose.yaml#L50).

### 13. P2 — One Kaggle allocation still has an unknown creation/cleanup outcome

**Retained operational evidence.** The Phase 8 `gpu-2` state remains `create_pending`, with no operation/session receipt and `KAGGLE_CREATE_OUTCOME_UNKNOWN`. Its fixed expiry has passed. The recorded notebook-version Error status cannot establish allocation cancellation. Other identified sessions have cancellation receipts, with gateway revocation independently observed for one.

**Impact and fix.** Resource cleanup cannot be certified for this creation. Reconcile that exact owned notebook/session through provider evidence and retain the result. Keep unknown creation as an explicit error. Do not retry that creation as a new allocation. The audit made no new GPU allocations. [Creation state](../../backend/documind/kaggle_session.py#L185) · [Watcher](../../scripts/kaggle_serving_probe.py#L372) · [Prior lifecycle evidence](phase8-benchmark-2026-11-15.json).

### 14. P2 — Two Python dependency advisories remain applicable

**Fresh scan.** The installed environment has `accelerate==1.13.0` and `pytest==8.4.2`. The scanner emitted the pytest entry twice, yielding two unique advisories. Accelerate has a sharded-checkpoint path traversal/denial-of-service advisory with no patched version listed. Pytest's temporary-directory advisory is fixed in 9.0.3, which the current `<9` development constraint excludes. Their impact requires the advisory's conditions. The audit demonstrated no exploit through DocuMind. [Accelerate advisory](https://github.com/advisories/GHSA-4j2p-28q2-5m79) · [Pytest advisory](https://github.com/advisories/GHSA-6w46-j5rx-g56g).

**Fix.** Update and verify the pytest development constraint/lock. Keep checkpoints constrained to trusted immutable inputs and validate an upstream Accelerate mitigation or compatible patched release before broadening model intake. The frontend production dependency scan found zero listed advisories. [Pins](../../pyproject.toml#L32).

### 15. P2 — The deliverable still depends on an unpublished working tree

**Verified.** Local HEAD and remote `main` both remain at `a7a9a7b7db59d922ae036c6cc270b0039c5dad51`, the Phase 1B publication. Later implementation is modified or untracked. The earlier private source clone establishes local snapshot execution. A normal remote clone cannot reproduce the current project.

**Impact and fix.** The current implementation lacks a durable reviewed release revision and matching remote CI. Review and commit the scoped changes, run the candidate from a clean checkout and publish it with matching CI evidence. Keep `.data`, credentials and model weights excluded. Broader Phase 8 research also retains open human/RAGAS and representative pre-training data gates. [Current status](../../PROGRESS.md) · [CI definition](../../.github/workflows/tests.yml).

## Additional bounded limitations

- Four stalled DNS lookups occupy all four resolver slots until the underlying OS calls return. A subsequent healthy lookup times out even after replacing the stalled resolver in the probe. This is the current bounded-thread tradeoff. Recovery can require waiting for OS resolution or restarting the worker. [Resolver](../../backend/documind/chat.py#L83).
- Initializing an existing object root does not tighten its permissions. A restored 0755 root stayed 0755 in a probe, while the stored original remained 0600. No document-byte disclosure was reproduced. Review restored directory ownership and permissions as part of recovery. [Store initialization](../../backend/documind/storage.py#L43).
- The trained FP16 MPS profile still lacks a successful complete target answer on the tested 16 GB Mac. Synthetic broader Self-RAG accuracy was 81%, versus 86% baseline, with lower citation recall and higher latency. These are retained measured limitations. [Evaluation](phase8-benchmark-2026-11-15.md).

## Verification and boundaries

- Full suite ran now with OCR, cached retrieval, local chat model, trained CPU pilot and both Chromium modes. **297 passed, zero skipped**, with five dependency warnings. Frontend typecheck/build, Ruff, lockfile and whitespace checks passed.
- Eight disposable Python probes passed assertions that confirm observed defects or hardening limits. A Chromium probe reproduced connection receipt loss. Separate report probes accepted an incomplete 199-arm comparison and missing/corrupt metadata. Raw saved-wire inspection found 64 omitted candidates and 40 ambiguous global retrieval questions.
- Qdrant testing created and removed only its unique synthetic collection. PostgreSQL probes used disposable test databases. The persistent database stayed at `0009_split_reflection`. The audit created no application fixes, migrations on persistent data or GPU allocations.
- A known-format credential scan examined Git-eligible files. Two hits were documentation anchor text containing `mask-`, and were manually classified as false positives. This scan does not prove the absence of every possible secret format.
- The graph's source coverage checks found no recorded issue in cited files. Generated caches, dependencies and private `.data` evidence were read directly where relevant. The source review prioritized trust boundaries and failure paths. It did not read every dependency implementation or rerun training.
- Container OS/image advisories, a fresh public-data benchmark, human review, production load exhaustion, a PDF code-execution exploit and physical provider GPU deallocation were outside the executable checks. Current dependency advisories were checked against GitHub's reviewed records.

Private probe code, logs, JUnit and artifact bindings live in `.data/deep-audit-2026-11-16/`. [Machine-readable evidence](deep-audit-2026-11-16.json).
