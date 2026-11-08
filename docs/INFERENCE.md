# Inference connections and deployment contract

Status: accepted design under ADR-021; no connector, hosted runtime or trained release exists yet. This file owns execution placement, capability checks and the model-service boundary. DATA_MODEL owns persistent enums; API_CONTRACT owns the public API; SELF_RAG_SPEC owns model semantics; SECURITY owns credentials and outbound networking.

## 1. Two connection options

| Connection | User supplies | DocuMind responsibility | Available result modes |
|---|---|---|---|
| `managed_kaggle` | Authorized Kaggle account access, including an API credential where supported | Provision/reconcile a private notebook, load an approved trained release, establish an authenticated endpoint, monitor health/quota and stop owned resources | `self_rag` only after release and protocol gates; baseline only if independently supported and tested by the runtime |
| `custom_endpoint` | Approved endpoint URL, model ID and endpoint credential | Validate the connection, invoke its supported protocol and enforce evidence checks | Ordinary generation: `baseline_rag`; compatible approved trained service: `self_rag` |

Connection type is independent of answer mode. `auto` keeps its API_CONTRACT baseline/comparison behavior; it never selects Self-RAG implicitly. Ordinary external generation requires no second local Llama or local critic. An adapter alone cannot generate or score: its matching base model is also required. A local critic evaluating a different external generator is outside this release.

The hosted app operates EmbeddingGemma 2, the MiniLM reranker, retrieval, policy, Decimal validation and independent claim verification. It sends only the required question/context/evidence to the selected generator. No generator gets direct database, object-store, workspace-search or tool access.

Fully local DocuMind runs those application components and storage on the user's system. Its custom endpoint may be the complete trained service on the same machine; no external generation API key is required. Loopback authentication exceptions are operator-configured for local-only deployment, never enabled by a hosted user-supplied URL. Selecting an external endpoint makes processing hybrid, not fully local.

## 2. Capability admission and immutable configuration

- Implement one generation interface with adapters for explicitly supported provider protocols. Begin Phase 2 with one tested ordinary chat endpoint protocol; do not promise arbitrary URL/API compatibility.
- Check connectivity using synthetic prompts only. Record the requested and reported model identity, context limits, protocol, streaming/cancellation support and verification timestamp. A provider alias without an immutable weight revision is marked provider-managed; never invent a checkpoint hash.
- A custom endpoint advertises baseline capability only after a real completion test. Self-RAG additionally requires an approved release manifest, matching tokenizer/token map/serializer/protocol, complete reflection-family scores and saved compatibility probes. A model name, prompt that prints tags, or self-declared `self_rag=true` is insufficient.
- Hosted Self-RAG admission requires operator approval of the release and endpoint deployment; local-only administrators may approve their own tested deployment. Protocol probes are compatibility evidence, not cryptographic proof of remote weights. Record the trust basis; do not use an unverified custom deployment as a reproducible research result.
- Every connection configuration has an immutable version. URL, model, protocol, credential rotation or release changes create a new version and invalidate readiness for that version. Stop/revoke invalidates the connection immediately, including old versions.
- Pin connection/version, capability snapshot, model identity and release manifest (where applicable) when accepting a model-backed query. Recheck current authorization, revocation, readiness and identity before each outbound step. Changed identity fails the run; it never silently selects a newer model.
- Connection selection and credentials belong to the creating principal and workspace. V1 does not share a user's paid API/Kaggle account with other workspace members. Connection administration also requires workspace access; guesses at IDs cannot confer access.

## 3. Fixed trained release

Train and evaluate a shared release once; document uploads and questions never mutate its weights. Deliberate improvements publish a new immutable release. Upgrades are explicit between runs.

The release manifest includes base model ID/revision, adapter hashes, trained embedding/output-head rows, tokenizer files and token-ID map, chat template, serializer hash, `selfrag-v1`/`critic-v1` versions, dependency/runtime versions, precision/quantization and evaluation/probe artifact references. Never distribute base weights without the required rights. A merged or quantized artifact is a separate tested release variant; recheck token identities and critic behavior after conversion.

Use Python Transformers + PEFT behind a small FastAPI model service first. Load the complete release once per process, keep request contexts isolated, and begin with one active generation per runtime. Ollama is eligible for ordinary baseline inference; its suitability for this custom trained release must be demonstrated before use. A GPU deployment is the target; the proposed 2-vCPU/16-GB app host has no measured Self-RAG serving capacity.

## 4. Model-service boundary: `documind-runtime-v1`

This is the private inference transport, distinct from the public `/api/v1` application API. It is a target contract, not an existing endpoint. HTTPS plus service authentication is required remotely. Ordinary provider chat APIs use their own tested adapter; they do not have to implement this protocol.

| Method and path | Request | Response |
|---|---|---|
| `GET /runtime/v1/capabilities` | Authenticated probe | Protocol version, fresh runtime instance ID, immutable release/manifest identity, supported modes, context limit and cancellation support; no secrets |
| `POST /runtime/v1/steps` | Run ID, unique step ID, request hash, mode, expected runtime instance ID and manifest hash, state, canonical episode context, remaining token budget, absolute deadline | Echoed run/step/hash, runtime instance and manifest identity, generated token IDs, candidate text, reflection labels/family probabilities, input/output token counts and finish/error reason |
| `POST /runtime/v1/steps/{step_id}/cancel` | Authorized cancellation for that caller's step | Idempotent cancellation acknowledgment or already-finished status; no promise to recall completed computation |

Namespace step IDs/results by the authenticated caller. A service restart creates a new runtime instance ID; reject requests for a previous instance before inference, so deduplication loss cannot silently rerun a step. Pin that instance along with the release for trained runs. Ordinary providers without recoverable idempotency fail uncertain calls rather than retrying them.

The step state is DECIDE or CANDIDATE for Self-RAG, following SELF_RAG_SPEC exactly. A baseline step uses ordinary generation with reflection fields absent and is supported only if advertised/tested. Context contains the question, permitted committed segments and selected evidence bundle; serialize with the exact training serializer. Treat all document/question content as data, escape control symbols, and never accept executable code or user-supplied artifact download paths.

The model service stops at the required token boundary and returns a complete bounded step. DocuMind owns the state machine, mandatory retrieval overrides, candidate ranking, evidence ledger and final answer. After retrieval it reconstructs context and requests the next step. No per-token network round trips or preserved cross-request KV cache are required.

For reflection scoring, return probabilities for every label in each emitted family, computed from the model logits restricted to that family as SELF_RAG_SPEC specifies. Use scores before sampling penalties or constraints distort those values. Arbitrary top-k log-probabilities or a generated confidence string are insufficient. Validate finite probabilities, family membership/sums, token IDs, allowed transitions, identities, response size and token budgets before consuming the result. The backend recomputes critic-v1 ranking and applies the independent evidence gate. Remote output is never a tool command or proof of source support.

The existing per-run limits (including 120 seconds and 2048 newly decoded tokens for Self-RAG) apply across network calls and all candidates; transport must not reset them. Persist completed steps and consumed budgets before dispatching another step. A repeated step ID with the same hash returns an existing result or in-progress status within the live runtime; a different hash conflicts. On a worker/runtime restart or ambiguous timeout, an unconfirmed in-flight step is not automatically regenerated: fail the run unless its original result and usage can be recovered. Confirmed completed steps can resume under the original deadline and budget. Never claim exactly-once physical inference.

Cancellation, source deletion, connection revocation and deadline expiration prevent further steps; send best-effort cancellation and discard late results. API_CONTRACT still governs exactly one durable terminal event. Stop/cancel may not stop provider billing immediately. Keep candidate text internal; public SSE remains the backend's verified event stream and does not depend on upstream SSE support.

## 5. Managed Kaggle lifecycle

This option is a committed product target with implementation/platform gates pending, not a guaranteed GPU entitlement. Before enabling it, establish that notebook serving/tunneling and third-party account management are permitted, validate supported credential/OAuth scopes and lifecycle operations, and verify model access, network access and actual GPU availability. Keep `MANAGED_KAGGLE_ENABLED=false` until those gates pass. Do not bypass quotas or platform session restrictions.

1. Accept credentials through the authorized connection setup flow; verify account identity and permitted operations. Where available and suitable, use scoped OAuth rather than retaining a broad personal credential. Never reuse the developer's configured Kaggle account for product users.
2. Explicit start creates a durable provisioning job. Use a private, versioned notebook template owned by DocuMind's integration, an approved release hash and per-connection endpoint credentials. Never include uploaded documents or secrets in notebook source, outputs, metadata or logs. Verify private visibility before provisioning; use a supported secret delivery mechanism and report a blocker if none can be secured.
3. Reconcile an external resource identity before retrying a create call; a lost response must not start duplicate GPU notebooks. Persist notebook/session IDs and lease/fencing state. Only manage resources created for this connection; never delete the user's unrelated notebooks.
4. Load the release and start the authenticated model service and a permitted tunnel/gateway. Persist its discovered URL/endpoint-credential reference separately from the account credential as a new immutable connection version using the provisioning job's expected-version and lease checks; an obsolete job cannot publish over a user edit/revoke. Health and compatibility checks for that exact version must pass before `ready`. Notebook provisioning/model loading is separate from query execution; queries do not wait indefinitely for a GPU or reset their timeout.
5. Detect expiry, unavailable capacity, quota/rate limits, bad credentials and endpoint loss. Mark the connection unavailable with a typed reason; finish/fail in-flight work according to the protocol and stop dispatching new work. Report quota as unknown when the platform cannot provide it; never promise fixed weekly hours.
6. Explicit stop/revoke prevents new use immediately and schedules cleanup through durable jobs. Stop owned runtime/tunnel resources using supported platform operations; report pending/failed cleanup if it cannot be confirmed. Do not promise that removing a credential guarantees the notebook has stopped. Revoke the endpoint token and remove retained credentials when safe cleanup completes; allow immediate local credential erasure with an explicit remote-cleanup warning. User can always stop remaining resources through Kaggle.

Revoking a custom endpoint removes DocuMind's credentials/configured access and cancels its own requests; it never shuts down or deletes the user's externally managed server. Remote runtime cleanup applies only to resources provisioned by the managed connector.

## 6. Missing key, exhausted quota and endpoint fallback

- No Kaggle connection is required for custom-endpoint use. Ordinary remote providers require an endpoint credential; trusted loopback deployments may omit it under the local-only exception.
- If Kaggle is unavailable, show the reason and let the user configure/select a custom endpoint. A ready custom endpoint can also be selected without first trying Kaggle.
- Switching connection or mode creates a new query with a new idempotency key. Preserve the original run's outcome. Never silently reroute evidence, retry across providers, or relabel a failed Self-RAG run as baseline.
- Show the destination and available mode before submission. An ordinary endpoint offers baseline; an admitted trained service may offer Self-RAG. If no generator is ready, keep upload, preview and deterministic comparison available when their own dependencies are ready; do not fabricate generated answers or pass a model-baseline release gate on templates alone.

## 7. Deployment and validation boundaries

For hosted deployment, keep embedding/reranking and policy in the app environment; generation lives at the selected endpoint. Start the proposed 2-vCPU/16-GB host with small batches, bounded work and measured contention between indexing, reranking and OCR. Model capacity, concurrent users and latency are unmeasured. Persistent metadata, documents and indexes need durable storage; ephemeral demo disks cannot be the only copy.

For local deployment, use the same contracts and compatible endpoint service. Do not require an external provider key merely because Self-RAG is enabled locally. Avoid running separate baseline and trained copies simultaneously on limited hardware; select the needed tested release between runs.

Build gates: Phase 2 ordinary endpoint integration; Phase 3 connection UI and explicit failures; Phase 5 immutable trained bundle; Phase 6 custom Self-RAG transport and probes; Phase 6K managed Kaggle provisioning after platform/security gates. Phase 0/1A, baseline release and deterministic comparison remain independent of training and Kaggle. See TESTING scenarios N-P.

References for implementation verification: [PEFT](https://huggingface.co/docs/peft/index), [Transformers generation](https://huggingface.co/docs/transformers/main/en/main_classes/text_generation), [Kaggle API](https://www.kaggle.com/docs/api), [Kaggle terms](https://www.kaggle.com/terms), [Kaggle notebooks](https://www.kaggle.com/docs/notebooks), [Ollama authentication](https://docs.ollama.com/api/authentication). Recheck platform behavior and terms when implementing; these links do not establish deployment approval or a working service.
