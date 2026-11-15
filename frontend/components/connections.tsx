"use client";

import { useRef, useState, type FormEvent } from "react";
import { ApiError, explain, mutate, waitJob, type Connection } from "../lib/api";

export default function Connections({
  base, items, refresh,
}: { base: string; items: Connection[]; refresh: () => Promise<void> }) {
  const [editing, setEditing] = useState<Connection | null>(null);
  const [busy, setBusy] = useState(false);
  const active = useRef(false);
  const submission = useRef<{ path: string; method: "POST" | "PATCH"; body: object; key: string; uncertain: boolean } | null>(null);
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const path = base + "/inference-connections";

  async function action(work: () => Promise<void>) {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setError("");
    setStatus("");
    try {
      await work();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      try {
        await refresh();
      } catch (reason) {
        setError((previous) => previous || (reason as Error).message);
      }
      setBusy(false);
      active.current = false;
    }
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current) return;
    const form = event.currentTarget;
    if (!submission.current) {
      const data = new FormData(form);
      submission.current = {
        path: editing ? path + "/" + editing.id : path,
        method: editing ? "PATCH" : "POST",
        key: crypto.randomUUID(),
        uncertain: false,
        body: {
          display_name: data.get("display_name"), endpoint_url: data.get("endpoint_url"),
          model_id: data.get("model_id"), api_key: data.get("api_key") || null,
          ...(editing ? { expected_config_version: editing.config_version } : {}),
        },
      };
      (form.elements.namedItem("api_key") as HTMLInputElement).value = "";
      setPending(true);
    }
    const sent = submission.current;
    void action(async () => {
      try {
        await mutate<Connection>(sent.path, sent.method, sent.body, sent.key);
      } catch (reason) {
        if (!sent.uncertain && reason instanceof ApiError && reason.status >= 400 && reason.status < 500) {
          submission.current = null;
          setPending(false);
        } else {
          sent.uncertain = true;
        }
        throw reason;
      }
      submission.current = null;
      setPending(false);
      form.reset();
      setEditing(null);
      setStatus("Connection saved. Validate it before use.");
    });
  }

  return (
    <details className="panel connections">
      <summary><strong>Inference connections</strong><span className="muted">Choose your generator</span></summary>
      <p className="muted">DocuMind runs on this device. Your generator can run locally, through your private Kaggle bridge, or at an API endpoint you select.</p>
      <p className="muted">Trained reflection uses the separately configured fine-tuned runtime on your device or Kaggle. Choose it in Answer mode when it is ready.</p>
      <ul className="connection-list">
        {items.map((item) => <li key={item.id}>
          <div>
            <strong>{item.display_name}</strong> <span className="badge">{item.status}</span>
            <p className="muted destination">{item.endpoint_url}</p>
            <small>{item.model_id} · v{item.config_version} · {item.supported_modes.includes("baseline_rag") ? "Baseline RAG" : "No validated mode"}</small>
            {item.error_code && <p className="error">{explain(item.error_code)}</p>}
          </div>
          {item.status !== "revoked" && <div className="actions">
            <button type="button" disabled={busy || pending} onClick={() => void action(async () => {
              const receipt = await mutate<{ job_id: string }>(path + "/" + item.id + "/validate", "POST");
              await waitJob(base, receipt.job_id, (job) => setStatus("Validation · " + job.status));
              setStatus("Endpoint validated for baseline RAG.");
            })}>Validate endpoint</button>
            <button type="button" disabled={busy || pending} onClick={() => setEditing(item)}>Replace credential or edit</button>
            <button type="button" disabled={busy || pending} onClick={() => void action(async () => {
              await mutate(path + "/" + item.id, "DELETE");
              if (editing?.id === item.id) setEditing(null);
              setStatus("Connection revoked.");
            })}>Revoke</button>
          </div>}
        </li>)}
      </ul>
      <form className="connection-form" key={editing?.id ?? "new"} onSubmit={save}>
        <h3>{editing ? "Edit " + editing.display_name : "Add a generation endpoint"}</h3>
        <fieldset disabled={pending || busy}>
        <label>Connection name<input name="display_name" required maxLength={80} defaultValue={editing?.display_name} /></label>
        <label>Chat completions URL<input name="endpoint_url" type="url" required maxLength={512} placeholder="https://provider.example/v1/chat/completions" defaultValue={editing?.endpoint_url} /></label>
        <label>Model ID<input name="model_id" required maxLength={256} defaultValue={editing?.model_id} /></label>
        <label>API key<input name="api_key" type="password" maxLength={4096} autoComplete="new-password" /></label>
        <small className="muted">A key is required for public HTTPS endpoints. It can be omitted for the operator-authorized loopback endpoint.</small>
        </fieldset>
        {pending && <p role="status">Save not confirmed. Retry the same request to recover its receipt. The credential is held only in memory until confirmed.</p>}
        <div className="actions">
          <button type="submit" disabled={!base || busy} formNoValidate={pending}>{pending ? "Retry save" : editing ? "Save new version" : "Save connection"}</button>
          {editing && <button type="button" disabled={busy || pending} onClick={() => setEditing(null)}>Cancel edit</button>}
        </div>
      </form>
      {status && <p role="status" className="muted">{status}</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </details>
  );
}
