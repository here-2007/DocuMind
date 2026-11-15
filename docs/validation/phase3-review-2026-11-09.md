# Phase 3 implementation review

This audit reproduced six workflow defects and two event API discrepancies. The Phase 4 work resolved them. [Resolution](#resolution).

## Verified paths

- TypeScript, the Next.js production build, Ruff, the Python lockfile check and Git whitespace checks passed. All 66 source and configuration hashes matched the earlier [validation artifact](phase3-2026-11-08.json).
- The full suite passed 97 tests with zero skips and five PyMuPDF/SWIG warnings in 96.88 seconds. It exercised real OCR, EmbeddingGemma 2, MiniLM, Redis/Celery, PostgreSQL, Qdrant and a real SmolLM2 model. The Chromium test used the running local stack with a synthetic completion endpoint. The suite verified upload through page 2 citation highlighting, cancellation, worker recovery and baseline trace types.
- Additional Chromium probes used the built Next.js UI with intercepted API responses to control timing and state changes. Separate probes used the real FastAPI routes and disposable PostgreSQL/Qdrant data. All probes used synthetic content. The code graph predates these paths, so the review relies on source and execution.

## Workflow findings

| ID | Priority | Reproduction and result | Expected behavior |
|---|---|---|---|
| P3-F1 | Major | Deliver only event 1, close the stream, then report a succeeded query at the result endpoint. The browser shows the verified answer and one trace event. It sends no second subscription even though the remaining events are available. [Trace client](../../frontend/lib/trace.ts#L48) | A disconnect before the terminal frame should replay after the last sequence even if the result is already terminal. A result read alone cannot confirm trace delivery. |
| P3-F2 | Major | Open a preview needing review and check the confirmation box. Change the same document's preview to another extraction needing review, then click Refresh. Confirmation remains checked and submitting sends the newer extraction ID. [Confirmation state](../../frontend/components/workspace.tsx#L21), [review submission](../../frontend/components/workspace.tsx#L133) | Bind the attestation to the document and extraction version the user reviewed. A different version needs a new attestation. |
| P3-F3 | Major | Submit a query without a worker claiming it, move its persisted deadline into the past and run the dispatcher selector three times. All three selections still include the query. Result status stays queued, and the ledger only has the initial policy decision. [Dispatcher](../../backend/documind/repository.py#L788), [deadline contract](../API_CONTRACT.md#4-events-cancellation-and-source-drilldown) | The dispatcher must finish an expired queued run with a terminal timeout even while workers are unavailable. |
| P3-F4 | Major | Interrupt an upload response and submit the same file again from the unchanged form. Chromium sends two different idempotency keys. A separate API probe confirmed that replaying the original key returns the same document/job, while the second key creates a distinct pair. An accepted upload with a lost receipt can therefore be duplicated by retry. [Upload client](../../frontend/lib/api.ts#L97), [form behavior](../../frontend/components/workspace.tsx#L108) | Reuse the operation key for a retry after an unknown outcome. Create a new key for an intentional new upload. |
| P3-F5 | Major | A real loopback 429 marks the connection unavailable in the API. In the UI probe, the terminal quota error leaves that endpoint selected and offered as ready, with no connection refresh. An abstaining run with `inference: null` also shows “Endpoint used for this run.” [Result handler](../../frontend/components/query-chat.tsx#L38), [destination label](../../frontend/components/query-chat.tsx#L99), [backend status update](../../backend/documind/phase_jobs.py#L103) | Refresh capabilities after endpoint failure and distinguish the selected destination from actual provider use. |
| P3-F6 | Major | Open a cited answer, then refresh after the document is removed from the server's list. The library becomes empty while the prior answer, quote and highlighted page remain visible. A separate API probe confirmed the real result route returns 410 after deletion and citation drilldown is blocked. This is stale browser state after a refresh, with no new disclosure by the API. [Workspace refresh](../../frontend/components/workspace.tsx#L28), [retained chat result](../../frontend/components/query-chat.tsx#L96), [pinned viewer](../../frontend/components/source-viewer.tsx#L12) | Invalidate displayed results and source previews when the refreshed workspace or result authorization indicates revocation. |

## Event API discrepancies

- **P3-F7a.** `Accept: text/event-stream;q=0, application/json` still starts SSE. The route checks the media type name and ignores the explicit rejection. [Route](../../backend/documind/phase2_api.py#L180), [HTTP quality values](https://www.rfc-editor.org/rfc/rfc9110.html#section-12.4.2).
- **P3-F7b.** An expired JSON event request returns 410 with an empty `details` object. The SSE equivalent includes the documented result URL. [Events route](../../backend/documind/phase2_api.py#L180), [SSE expiry response](../../backend/documind/query_stream.py#L50).

Local reproductions are in ignored `.data/phase3-review/browser.cjs` and `.data/phase3-review/test_backend_probes.py`. Run the browser probe after `npm --prefix frontend run build` with `node .data/phase3-review/browser.cjs`. Run the backend probes with `DOCUMIND_TEST_RETRIEVAL=1 uv run --no-sync python -m pytest -q --tb=short .data/phase3-review/test_backend_probes.py`. Expected-failure assertions reproduce the dispatcher and two API discrepancies. Supporting assertions establish the API's real upload, quota and revocation behavior. Application source and existing tests were unchanged.

External HTTPS inference, remote CI, held-out evaluation and the proposed 2-vCPU deployment remain unverified. They are separate from the reproduced local defects.

## Resolution

The Phase 4 suite verifies replay through an already-finished run, exact review-version binding, a stable upload key after a lost accepted receipt, connection refresh after a quota error, accurate endpoint-use labels and removal of revoked answers and highlights. The backend regression verifies one queued timeout without worker delivery and checks negotiation and result links for expired JSON/SSE requests. [Current verification](../../PROGRESS.md).
