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

Local prototype may be single-user only if explicit. Before public deployment: authenticate every route, authorize workspace membership, enforce access at DB query, object download, Qdrant payload and BM25 results. Add tests that intentionally index identical private-looking content in separate workspaces and prove no cross-scope hits.

Do not rely on browser-side filtering; server must enforce. Avoid sending existence clues via error messages. Apply DATA_MODEL.md scoped composite constraints and INGESTION.md snapshot/visibility checks. Lifecycle deleting blocks all reads immediately, including cached answers, reports, source downloads and event replay. V1 downloads are API-proxied, not public or long-lived signed URLs. Local-only mode binds the API/services to loopback and is not enabled on an internet-facing deployment.

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
- Prevent SSRF for all endpoint validation and inference: accept only supported HTTPS URLs/ports, reject URL userinfo and query-string secrets, block private/loopback/link-local/metadata/reserved addresses for both IPv4 and IPv6, validate DNS at connection time, and disallow redirects. Enforce egress controls against DNS rebinding; TLS certificate verification stays enabled. The local-only loopback exception is operator-controlled and unavailable to hosted user input. Kaggle-generated tunnel URLs pass the same checks.
- Authenticate the public tunnel/gateway; expose only necessary inference/capability/cancel routes. Do not expose model pull/delete, shell or notebook administration. Bound request/response sizes, time, queue and model concurrency. Browser calls go through DocuMind; a hidden URL or CORS policy is not authentication.
- Connect/start/stop/config changes require ownership and workspace authorization, CSRF protection where cookie-authenticated, durable idempotency and audit metadata without secrets. Revoke blocks new and subsequent steps immediately; discard late output and cancel best effort. Keep cleanup truthful when platform access fails or credentials are erased.
- Managed Kaggle uses private, versioned templates and a supported secure secret-delivery mechanism. Validate visibility before starting. Reconcile only owned resources; no quota evasion, keepalive tricks or developer credential fallback. Platform permission and authenticated tunnel support are enablement gates, not assumptions.
- Treat endpoint capability manifests and completions as untrusted claims: verify against approved release identities/probes, validate schema/budgets and retain independent source checks. Unknown custom servers cannot establish their real weights merely by reporting a matching hash.

## Implemented local boundary (ADR-022)

The native backend requires a generated local Bearer token on data routes and rejects workspaces other than its configured local workspace; health/schema documentation contain no private source data. Compose publishes ports only on loopback. This is not public user authentication. Private filesystem objects use restricted file permissions and are served only through scoped API reads. Native parsing is isolated with page/pixel/text/render-byte/deadline limits and a memory-limited worker container. PostgreSQL blocks mutation of promoted source records and invalid scoped references. Exceptions/logs omit raw document bodies and secret strings. Public auth, network-facing abuse protection, dependency vulnerability scanning and hosted storage/inference security remain separate release gates.
