# Security, privacy and trust boundaries

The original Self-RAG PDF does not specify a document application threat model. These safeguards are **additional architecture** introduced because DocuMind processes potentially confidential files and exposes source-grounded results.

## 1. Threat model

- **Malicious uploads:** malformed PDFs/images, archive bombs, decompression spikes, oversized files, parser vulnerabilities.
- **Prompt injection from documents:** PDF text instructing the model to ignore the user's query, reveal system prompts, call external URLs or misreport amounts.
- **Cross-workspace leaks:** dense vector retrieval, BM25 search, SQL query or cached results returning another workspace's document content.
- **Data exfiltration:** external LLM/annotation calls, public object store URLs, logs, error traces, telemetry or report sharing.
- **Incorrect business claims:** OCR reading `1000` as `10000`, hallucinated source, mixed currencies or unsupported claims presented as verified.
- **Resource exhaustion:** high page-count scans, recursive documents, simultaneous GPU generation, excessive retrieval loops.

## 2. File boundary

1. Allowlist actual file types and inspect content signature; reject executables/scripts masquerading as PDFs.
2. Set upload, rendered page, megapixel, text-length, decompression and job time limits.
3. Run parsers in resource constrained worker containers using least privilege; keep libraries patched.
4. Never execute macros from Office files or embedded scripts from PDFs.
5. Write originals to private storage, use nonpredictable keys, encrypt in transit, configure retention/deletion.
6. Handle all filenames as display metadata, not filesystem paths.

## 3. Identity and isolation

DocuMind is a single-operator local application under ADR-042. Require local bearer access on data routes and preserve workspace checks in the database, object downloads, Qdrant and BM25. Separate-workspace tests must prove that no source crosses scope.

Enforce access on the server and avoid existence clues in errors. Keep scoped constraints and immutable source checks. Deletion revokes source reads, cached answers, reports and event replay immediately. Bind app listeners and published ports to loopback.

## 4. Prompt injection policy

- Surround retrieved passages in a fixed evidence template clearly marked **untrusted source content**.
- The retriever returns strings/data, not tool requests. Disable implicit browsing, shell, code execution or arbitrary URL fetch from model outputs.
- Do not parse `[Retrieve]` strings inside document content as model control tokens. Control originates solely from generated token IDs, never cited content.
- Never let a document redefine policy, role, tool configuration, workspace scope or retrieval budget.
- Require citations and deterministic validators on factual/numeric assertions. Critics and LLM self-assessments are **not** security controls by themselves.

## 5. External inference and dataset ethics

- Training/annotation providers must be explicitly configured, approved and documented.
- Default to synthetic or appropriately licensed public documents when using hosted teacher models.
- Redact/exclude sensitive personal, financial or proprietary data from logs, evaluation fixtures and public demos.
- Store API credentials in environment/secrets management only; never commit or display them.
- Check model and dataset license terms before redistribution or public demo.

## 6. Calculation and explanation safety

`Decimal` and rule-based comparison compute numeric findings; LLM explanations are downstream renderings only. Where currencies, effective contract amendments, tax rates or document identities are uncertain, return `needs_review`/`unverified` rather than implying wrongdoing or a legally binding conclusion.

## 7. Release checklist

- [ ] Parser dependency/security scan; content/size limits enforced.
- [ ] Authorization tests for API, object store, Qdrant, BM25, reports, traces.
- [ ] No secrets or private uploaded files committed; sanitized logs.
- [ ] Uploaded prompt injection examples fail to override instructions or data scope.
- [ ] Numeric mismatch and ambiguous linking cases abstain or flag review correctly.
- [ ] Rate limits, job timeouts, max 3 retrieval calls, error handling and deletion flows tested.
- [ ] Dataset/model licensing and third-party privacy approvals recorded.

## 8. Credential and environment handling

`.env.example` contains only proposed nonsecret defaults and empty credential slots. Actual environment files, Kaggle credentials, model tokens and private uploads remain outside Git and logs. Configured local Kaggle authentication may be used directly when a task requires Kaggle; never copy credential file contents into project docs, command output or examples. Credential configuration does not prove dataset/model authorization, GPU availability or license compatibility.

## 9. User inference credentials and outbound requests

- Hosted connection secrets are scoped to the creating principal and workspace, stored encrypted in a secret manager; application records contain opaque references only. Never put a user's key in shared process-global environment, URLs, notebook source, browser storage, analytics, traces or exception output. Transport secret submissions over TLS; secret fields are write-only. Local installation may use private environment configuration. Secret-bearing idempotency hashes must be keyed and protected.
- API/Kaggle credentials authorize their intended service only. A separate short-lived or rotatable gateway token protects inference; never send a Kaggle account credential to the model endpoint. The model service gets no database, object-store or developer-account credentials. No user's account may be consumed by another workspace member without a future explicit sharing design.
- Show the configured destination and data-handling terms before sending question/evidence text. Send only required context; disable prompt/body logging and persistent notebook outputs. Uploading a document does not authorize training on it. Third-party retention/deletion limits must be disclosed; stopping a runtime cannot recall already transmitted data.
- External generation endpoints use DNS-pinned HTTPS with certificate verification, no redirects and private/reserved IPv4/IPv6 rejection. The exact literal loopback URL exception is configured by the local operator. The Kaggle bridge validates its exact private session gateway separately.
- Authenticate both local bridge and upstream inference. Expose only selected inference/capability/cancel routes. Keep notebook administration and upstream execution credentials private. Bound request/response sizes, time and concurrency.
- Connect/start/stop/config changes require ownership and workspace authorization, CSRF protection where cookie-authenticated, durable idempotency and audit metadata without secrets. Revoke blocks new and subsequent steps immediately; discard late output and cancel best effort. Keep cleanup truthful when platform access fails or credentials are erased.
- Managed Kaggle uses private, versioned templates and a supported secure secret-delivery mechanism. Validate visibility before starting. Reconcile only owned resources; no quota evasion, keepalive tricks or developer credential fallback. Platform permission and authenticated tunnel support are enablement gates, not assumptions.
- Treat endpoint capability manifests and completions as untrusted claims: verify against approved release identities/probes, validate schema/budgets and retain independent source checks. Unknown custom servers cannot establish their real weights merely by reporting a matching hash.

## Implemented local boundary (ADR-022)

The backend requires a local Bearer token and checks the configured workspace. Files retain private permissions. Native parsing keeps page, pixel, text and deadline limits. PostgreSQL blocks promoted-source mutation and invalid scoped references. Logs omit documents and secrets. External model calls require explicit selection and encrypted local credentials.

The PDF subprocess receives a minimal environment, a private input/output directory and read-only access to runtime libraries and configured OCR models. macOS Seatbelt or Linux Landlock and seccomp block network access and limit file access. Resource limits cap parser output and process memory, with wall-clock supervision and process group cleanup in the parent. Worker credentials and backing stores remain outside the parser's file and environment allowlists.

### Phase 2 local endpoint controls

The loopback Qdrant service requires a generated private `QDRANT_API_KEY`, shared with the index client. Compose disables its CORS middleware. Loopback binding alone does not protect an unauthenticated backing store. Upgrade older configuration using the [runbook](LOCAL_DEVELOPMENT.md#existing-qdrant-installations), preserving the index volume.

Keep `INFERENCE_SECRET_KEY` private and stable across the API and worker. Credentials are write-only and stored as ciphertext with owner and version binding. The endpoint version references that secret. Editing or revoking erases the old ciphertext and blocks stale dispatch. Request-key comparisons use a keyed digest for secret-bearing configuration bodies.

The chat adapter rejects URL credentials, query strings, redirects, unsafe ports, site-local IPv6 and nonglobal destination addresses. It resolves all addresses before the call and connects to one vetted numeric address with TLS verification for the original hostname. The exact operator-set loopback exception is limited to 127.0.0.1 or ::1. Authorization and the bounded HTTP request write hold the same workspace lock used by deletion, cancellation and connection changes. The lock releases before waiting for the provider response. An admitted call may finish upstream after cancellation. The worker discards late output and fails uncertain calls without replay.

### Phase 3 local browser boundary

Connection create/edit retains one immutable request and key until its receipt is confirmed. After an unknown outcome, retry reuses the exact credential, destination and expected version. The credential remains in transient component memory, with the visible input cleared and destination editing disabled. It is released on confirmation or a definitive first-attempt client rejection. A rejection during recovery does not erase the earlier unknown outcome. Reloading or leaving the page discards that memory; inspect saved connections before creating another after a reload.

Next.js listens on loopback and forwards same-origin requests to a fixed loopback API. The local Bearer token stays in server configuration. Endpoint credentials pass through the browser only during setup. The proxy rejects cross-origin, unexpected Host and Fetch Metadata headers. Protected responses use no-store caching.

## Local model and private Kaggle boundaries

The fine-tuned model and generator are independently selected. Keep their credentials separate and pin both identities. An external candidate is untrusted text. Labels come from the trained runtime, while the independent verifier checks supported claims.

The private Kaggle bridge accepts a local Bearer token and allowlists the chosen role's inference routes. Jupyter execution, file APIs and upstream gateway credentials stay private. The local operator command, original expiry, detached watcher and remote process guardian own lifecycle. Public collection of Kaggle keys is outside scope.
