# Local application and model execution contract

ADR-042 defines the current architecture. DocuMind's Next.js UI, FastAPI API, workers, PostgreSQL, private files, Qdrant, BM25, embedding model, reranker and evidence verifier run on the user's device. App hosting on Hugging Face Spaces or another public service is outside the project. Hugging Face remains a model registry used to download pinned weights.

## 1. Two independent model choices

| Role | Available placement | Interface |
|---|---|---|
| Fine-tuned reflection model | This device or the user's private Kaggle GPU session | Authenticated documind-runtime-v1 |
| Answer generator | This device, the user's private Kaggle GPU session, or a supplied endpoint and API key | OpenAI-compatible chat completions |
| Embedding, reranking, OCR and validators | This device | Existing internal services |

The fine-tuned model always includes its matching base, adapter, saved token rows and tokenizer. The selected generator may use a different model. One compatible trained runtime can also generate its own candidates for the integrated research path.

Local operation describes where the application and stores run. Choosing Kaggle or an external generator sends the question and selected evidence to that destination. Show the selected destination before a run. Credentials stay in the local application or private bridge, and never enter model prompts.

## 2. Answer execution

- Evidence-only baseline retrieves and validates direct source fields without model inference.
- Baseline with a generation connection calls that selected generator and independently checks its answer.
- Trained reflection without a generation connection retains integrated checkpoint inference for compatible existing runs and controlled research.
- Trained reflection with a generation connection uses the trained model for retrieval decisions, calls the selected generator for candidate text, and returns that exact text to the trained model for relevance, support and utility labels. The app computes critic-v1 ranking and independently verifies citations and arithmetic.

The split path reports mode `self_rag` and inference strategy `split_reflection`. The integrated path reports strategy `integrated_checkpoint`. The split path is a trained Self-RAG-inspired adaptation. Research comparisons record both models and use the same base for controlled attribution. Generators alone cannot advertise trained reflection capability.

Document questions require selected, authorized evidence. Eligible general queries follow the bounded grammar in SELF_RAG_SPEC. Selection, failures and provider changes are explicit new runs.

## 3. Fine-tuned runtime

The runtime loads one immutable bundle once. It validates base revision, protocol/serializer hash, tokenizer/template, every artifact, all 15 trained rows, reload identity and a decision probe. Reviewed operation additionally requires a recorded accepted case review and matching generated-evaluation artifact. Explicitly delegated agent review is identified separately under ADR-044 and supports local experimental validation, not human research evidence. `--pilot` explicitly enables experimental service capability, which the normal app rejects for Self-RAG admission.

Run `python -m documind.reflection_runtime` locally, or `scripts/run_kaggle_model.py --role reflection` for the private Kaggle bridge. The local administrator pins `SELF_RAG_RUNTIME_ORIGIN`, `SELF_RAG_RUNTIME_TOKEN` and `SELF_RAG_MANIFEST_SHA256`. Set `REFLECTION_LOCATION=local` or `kaggle` for the setup display.

`--device checkpoint` retains checkpoint precision and needs CUDA for an NF4 bundle. Explicit `--device cpu` or `mps` loads an FP32 CPU base or FP16 MPS base and the saved adapter. Capabilities report the actual `inference_profile`. Recheck quality and latency for that profile. Qwen's FP32 base needs substantially more memory than its NF4 version. The small CPU pilot verifies the local path, and target-device capacity remains a hardware-specific check.

| Method | Input | Output |
|---|---|---|
| GET /runtime/v1/capabilities | Bearer authorization | Immutable model identity, supported mode, token map, template hash, context limit, candidate scoring support and actual inference profile |
| POST /runtime/v1/steps | Pinned run/step/hash/instance, state, context, token budget and deadline | Echoed identities, tokens, text, family probabilities and finish reason |
| POST /runtime/v1/steps/{step_id}/cancel | Authorized step identity | Cancellation acknowledgement |

The step context uses the immutable selfrag-v1 serializer. An optional `candidate_text` field outside that context asks the runtime to score externally generated text. It is allowed only for CANDIDATE when `candidate_scoring=true`. Its exact bytes enter the request hash. The decoder conditions its support and utility labels on the supplied ordinary tokens and EndSegment boundary. It rejects special/control tokens, oversized segments and invalid round trips. It cannot rewrite the candidate. A critic can still be wrong, so independent verification is mandatory.

The optional field is omitted when absent, preserving earlier request hashes and saved integrated runs. Earlier runtimes default to no candidate scoring. An incompatible split request fails before dispatch.

The generator request requires complete field values verbatim, including currency and units. The critic receives the provider's exact returned text. The app does not add omitted units or accept reserved reflection strings as an external answer. Independent verification remains authoritative; a safe abstention on an answerable field is still an answer-coverage miss.

For supplied candidate scoring, the token sequence includes the externally supplied ordinary tokens and forced EndSegment boundary. `forced_tokens` identifies those positions. `output_tokens` conservatively charges the full sequence to the shared budget, while actual critic-generated tokens equal `output_tokens - forced_tokens`. Traces expose the forced count when present. External answer text is never reported as newly generated by the critic.

## 4. Budgets, recovery and provenance

Every new reflection step uses the earlier of the query deadline and the active inference-call budget. Save this exact deadline with its step ID so confirmed replay preserves the request hash; older journals without it retain their original query-wide deadline. A transport failure or invalid runtime response triggers bounded best-effort cancellation of that exact step before failing the query. The runtime deadline remains a second bound when cancellation cannot reach it. The process guardian kills its owned process group on every exit, including a normal or failed parent exit.

The app caches the exact trained tokenizer and verifies its IDs, template and vocabulary. It preflights the full context with its output reserve and checks returned token counts. Explicit source windows and paired invoice excerpts follow SELF_RAG_SPEC and retain exact source offsets. The 512-token checkpoint limit remains intact.

Each query pins the reflection runtime instance and manifest plus the selected generator connection version, capability check and model identity. Generation uses the existing DNS-pinned bounded transport and authorization guard. The private recovery journal records provider intent before dispatch, then the exact candidate and validated critic result. Confirmed recovery reuses the saved candidate and result. Uncertain provider or model work fails explicitly. Cancellation, deletion, credential revocation and deadlines fence subsequent dispatch and result commit.

Results retain the trained model identity, inference profile, strategy, generator connection/version and actual generator identity. Source-derived candidate text stays in private transient recovery data. Public traces contain bounded metadata and reflection labels. Terminal completion removes the recovery journal.

The bundled generator and reflection base loader accept an immutable Hub revision and load safetensors from the checked snapshot. Before model loading, validate sharded index names, resolve regular files inside the Hub cache and reject arbitrary local model directories. Adapter intake keeps its existing manifest and artifact checks. The installed Accelerate version still appears in dependency scans for CVE-2026-69112. The checkpoint checks mitigate the advisory in these DocuMind entry points.

## 5. Generator setup

A generation connection stores a model ID, endpoint URL and optional encrypted credential in the local database. Public HTTPS endpoints require a key. An exact operator-allowlisted literal loopback endpoint can omit a provider key, although the bundled local model server and Kaggle bridge require their own Bearer token.

DNS resolution, TCP setup, TLS negotiation and HTTP exchange share the request deadline. A temporarily busy bundled generator returns `GENERATOR_BUSY` and keeps its validated connection ready for an explicit new run. Provider quota failures retain their own error and connection behavior.

For local inference, use Ollama's OpenAI-compatible endpoint, another compatible local server, or `python -m documind.generator_runtime`. The bundled service requires a pinned model revision, bounded messages, deterministic generation and one active request. It binds to loopback. It does not assume every provider implements custom reflection tokens.

For Kaggle generation, run `scripts/run_kaggle_model.py --role generation` with a pinned model/revision. Configure its loopback bridge URL as a generation connection. For external generation, configure the provider's HTTPS chat-completions URL, model and key in the workspace.

## 6. Private Kaggle lifecycle

The operator CLI uses the locally configured Kaggle account. The application never collects public users' keys. Model provisioning is an explicit command, and query execution never creates a notebook.

The bridge creates a private owned notebook/session, retains the creation receipt and original expiry, uploads fixed model-service code, and exposes only authenticated inference routes on local loopback. The upstream Jupyter URL grants notebook execution and must stay in owner-only state. The bridge never exposes Jupyter APIs, public tunnels or its upstream token to the browser.

A detached watcher cancels the exact session at expiry. A remote process guardian bounds installation, weight loading and serving. Ctrl-C closes the bridge and requests cancellation. Retry status/stop with the retained state after a host interruption. Cancellation acceptance and gateway revocation are distinct observations. Physical GPU release can remain unconfirmed.

Kaggle allocation, quota and platform permission remain external constraints. Both roles may share one integrated model when hardware is limited, or use separate private sessions within the account's limits. The application keeps native preview, evidence-only QA and deterministic comparisons available during model outages.

## 7. Verification

Test local generator authentication, request limits, exact candidate scoring, both pinned identities, revocation, full-source evidence checks, worker recovery and loopback routing. Test private bridge route allowlisting and cleanup separately from model quality. Live Kaggle execution and specific local hardware must be reported as run or unrun.

The 73-case selected-model workflow and the same-base research comparison remain release evidence. A new external generator pairing requires evaluation of its own outputs and critic calibration. The local setup commands and current verification are in [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md) and [PROGRESS.md](../PROGRESS.md).
