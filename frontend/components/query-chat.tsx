"use client";

import { useEffect, useEffectEvent, useRef, useState, type FormEvent } from "react";
import { api, ApiError, explain, mutate, type Citation, type Connection, type DocumentInfo, type Receipt, type Run, type Trace } from "../lib/api";
import { follow } from "../lib/trace";

type Turn = {
  question: string; receipt: Receipt; trace: Trace[];
  result?: Run; disconnected?: boolean; error?: string; destination?: string;
  unavailable?: boolean;
  documents: string[];
};

type Submission = {
  key: string; base: string; question: string; documents: string[]; destination?: string;
  body: { question: string; document_ids: string[] | null; mode: "baseline_rag" | "self_rag";
    connection_id?: string; expected_config_version?: number };
};

function unavailable(turn: Turn, code = "SOURCE_UNAVAILABLE"): Turn {
  return {
    ...turn, trace: [], result: undefined, disconnected: false,
    question: code === "QUERY_RECHECK_REQUIRED" ? "Answer needs recheck" : "Source unavailable",
    error: explain(code), unavailable: true,
  };
}

export default function QueryChat({
  base, selected, connections, documents, openCitation, refreshConnections,
}: {
  base: string; selected: string[]; connections: Connection[]; documents: DocumentInfo[];
  openCitation: (citation: Citation) => void; refreshConnections: () => Promise<void>;
}) {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<"baseline_rag" | "self_rag">("baseline_rag");
  const [runtime, setRuntime] = useState({ self_rag: false, split_reflection: false, reflection_location: "local" });
  const [connectionId, setConnectionId] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [active, setActive] = useState<Receipt | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [pending, setPending] = useState<Submission | null>(null);
  const sending = useRef(false);
  const [cancelling, setCancelling] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const ready = connections.filter((item) => item.status === "ready" && item.supported_modes.includes("baseline_rag"));
  const connection = ready.find((item) => item.id + ":" + item.config_version === connectionId);
  const canAsk = mode === "self_rag"
    ? runtime.self_rag && (!connection || runtime.split_reflection)
    : selected.length > 0;

  useEffect(() => {
    const controller = new AbortController();
    api<typeof runtime>("/api/v1/ready", { signal: controller.signal })
      .then(setRuntime).catch(() => {});
    return () => controller.abort();
  }, [connections]);

  const revalidate = useEffectEvent(async (signal: AbortSignal) => {
    const present = new Set(documents.map((doc) => doc.document_id));
    const mark = (id: string, code = "SOURCE_UNAVAILABLE") => {
      if (!signal.aborted) setTurns((rows) => rows.map((turn) =>
        turn.receipt.query_run_id === id ? unavailable(turn, code) : turn,
      ));
    };
    await Promise.all(turns.filter((turn) => !turn.unavailable).map(async (turn) => {
      if (turn.documents.some((id) => !present.has(id))) {
        mark(turn.receipt.query_run_id);
      } else if (turn.result || turn.disconnected) {
        try {
          await api<Run>(turn.receipt.result_url, { signal });
        } catch (error) {
          if (error instanceof ApiError && ([404, 410].includes(error.status) || error.code === "QUERY_RECHECK_REQUIRED")) {
            mark(turn.receipt.query_run_id, error.code === "QUERY_RECHECK_REQUIRED" ? error.code : "SOURCE_UNAVAILABLE");
          }
        }
      }
    }));
  });

  useEffect(() => {
    const controller = new AbortController();
    void revalidate(controller.signal);
    return () => controller.abort();
  }, [documents]);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const id = active.query_run_id;
    const update = (fn: (turn: Turn) => Turn) => setTurns((rows) => rows.map((turn) =>
      turn.receipt.query_run_id === id && !turn.unavailable ? fn(turn) : turn,
    ));
    follow(active, controller.signal, (trace) => update((turn) =>
      turn.trace.some((event) => event.seq === trace.seq) ? turn : { ...turn, trace: [...turn.trace, trace] },
    ), setStatus).then(async (result) => {
      if (result.error && result.status === "failed") {
        await refreshConnections().catch(() => {});
      }
      if (controller.signal.aborted) return;
      update((turn) => ({ ...turn, result, disconnected: false, error: undefined }));
      setActive(null);
      setStatus("");
    }).catch((reason: Error) => {
      if (controller.signal.aborted) return;
      update((turn) => reason instanceof ApiError && ([404, 410].includes(reason.status) || reason.code === "QUERY_RECHECK_REQUIRED")
        ? unavailable(turn, reason.code === "QUERY_RECHECK_REQUIRED" ? reason.code : "SOURCE_UNAVAILABLE")
        : { ...turn, error: reason.message, disconnected: true });
      setActive(null);
      setStatus("");
    });
    return () => controller.abort();
  }, [active, refreshConnections]);

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending.current || active) return;
    if (!pending && (!base || !canAsk || !question.trim() || (connectionId && !connection))) return;
    const attempt: Submission = pending ?? {
      key: crypto.randomUUID(), base, question: question.trim(), documents: [...selected],
      destination: connection?.endpoint_url,
      body: { question: question.trim(), document_ids: selected.length ? [...selected] : null, mode,
        ...(connection ? { connection_id: connection.id, expected_config_version: connection.config_version } : {}) },
    };
    sending.current = true;
    setPending(attempt);
    setSubmitting(true);
    setError("");
    try {
      const receipt = await mutate<Receipt>(attempt.base + "/queries", "POST", attempt.body, attempt.key);
      setTurns((rows) => [...rows, {
        question: attempt.question, receipt, trace: [], destination: attempt.destination, documents: attempt.documents,
      }]);
      setPending(null);
      setQuestion("");
      setStatus("Query queued");
      setActive(receipt);
    } catch (reason) {
      setError((reason as Error).message);
      void refreshConnections().catch(() => {});
    } finally {
      sending.current = false;
      setSubmitting(false);
    }
  }

  async function cancel() {
    if (!active || cancelling) return;
    setCancelling(true);
    setError("");
    try {
      await mutate<Run>(active.result_url + "/cancel", "POST");
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setCancelling(false);
    }
  }

  return (
    <section className="panel chat" aria-labelledby="chat-title">
      <div className="panel-head">
        <div><p className="eyebrow">WORKSPACE CHAT</p><h2 id="chat-title">Ask your documents</h2></div>
        <span className="badge">{mode === "self_rag" ? "Trained reflection" : "Baseline RAG"}</span>
      </div>
      <div className="turns" aria-live="polite">
        {!turns.length && <div className="empty chat-empty"><strong>Start with a question</strong><span>Choose published documents to ask about an invoice number, date, or total.</span></div>}
        {turns.map((turn) => (
          <article className="turn" key={turn.receipt.query_run_id}>
            <h3>{turn.question}</h3>
            {turn.destination && !turn.unavailable && <p className="muted destination">
              {turn.result?.inference ? "Endpoint used for this run" : "Selected endpoint"} · {turn.destination}
            </p>}
            {turn.result?.answer && (
              <div className="answer">
                <span className="eyebrow">{turn.result.grounding_status === "supported" ? "VERIFIED ANSWER" : turn.result.grounding_status === "unverified" ? "GENERAL ANSWER" : "INSUFFICIENT EVIDENCE"}</span>
                <p>{turn.result.answer}</p>
                {turn.result.inference && <small className="muted">Model · {turn.result.inference.model_id}
                  {turn.result.inference.config_version && " · config v" + turn.result.inference.config_version}
                  {turn.result.inference.strategy === "split_reflection" && " · separate selected generator"}
                  {turn.result.inference.generator_model_id && " · generator " + turn.result.inference.generator_model_id}
                </small>}
                {turn.result.citations?.map((citation) => <button type="button" className="citation" key={citation.citation_id} onClick={() => openCitation(citation)}>Open citation · page {citation.page_number}</button>)}
              </div>
            )}
            {turn.result?.error && <p role="alert" className="error">{explain(turn.result.error.code)}</p>}
            {turn.error && !turn.result && <p role="alert" className="error">{turn.error}</p>}
            {turn.disconnected && !active && <button type="button" onClick={() => setActive(turn.receipt)}>Resume trace</button>}
            <details className="trace">
              <summary>Run trace · {turn.trace.length} events</summary>
              <ol>{turn.trace.map((item) => <li key={item.seq}>
                <span>{item.type.replaceAll("_", " ")}</span>
                {typeof item.data.decision === "string" && <small>{item.data.decision.replaceAll("_", " ")}</small>}
                {typeof item.data.id === "string" && <small title={item.data.id}>Chunk {item.data.id.slice(0, 8)}</small>}
              </li>)}</ol>
            </details>
          </article>
        ))}
      </div>
      <form className="ask-form" onSubmit={ask}>
        <div className="form-meta">
          <label>Answer mode <select disabled={!!pending} value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}>
            <option value="baseline_rag">Baseline RAG</option>
            <option value="self_rag" disabled={!runtime.self_rag}>Trained reflection ({runtime.reflection_location}){!runtime.self_rag && " · unavailable"}</option>
          </select></label>
          <label>Generation <select disabled={!!pending} value={connectionId} onChange={(event) => setConnectionId(event.target.value)}>
            <option value="">{mode === "self_rag" ? "Fine-tuned model also generates" : "Evidence only (local)"}</option>
            {connectionId && !connection && <option value={connectionId} disabled>Selected endpoint unavailable</option>}
            {ready.map((item) => <option key={item.id} value={item.id + ":" + item.config_version}>{item.display_name}</option>)}
          </select></label>
          <span className="muted">{selected.length} source{selected.length !== 1 ? "s" : ""} selected</span>
        </div>
        {connection && <p className="muted destination">Sends the question and {mode === "self_rag" ? "retrieved excerpts" : "verified excerpt"} to {connection.endpoint_url}</p>}
        {mode === "self_rag" && connection && !runtime.split_reflection && <p className="error">The configured fine-tuned runtime needs candidate scoring support for this generator.</p>}
        {connectionId && !connection && <p className="error">Select a ready endpoint or switch to evidence only for a new run.</p>}
        <label className="sr-only" htmlFor="question">Question</label>
        <textarea id="question" rows={2} maxLength={1024} disabled={!!pending} placeholder="What is the invoice grand total?" value={question} onChange={(event) => setQuestion(event.target.value)} />
        {pending && !submitting && <p className="muted">Submission not confirmed. Retry uses the original question, sources and destination. A new query may leave the original running.</p>}
        <div className="actions">
          <button className="primary" type="submit" disabled={!!active || submitting || (!pending && (!canAsk || !question.trim() || (!!connectionId && !connection)))}>{pending ? "Retry query" : "Ask question"}</button>
          {pending && !submitting && <button type="button" onClick={() => { setPending(null); setError(""); }}>Start new query</button>}
          {active && <button type="button" disabled={cancelling} onClick={cancel}>Cancel query</button>}
          {status && <span className="muted" role="status">{status}</span>}
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </form>
    </section>
  );
}
