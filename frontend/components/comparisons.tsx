"use client";

import { useEffect, useEffectEvent, useRef, useState, type FormEvent } from "react";
import { api, ApiError, comparisonInvalid, explain, list, mutate, waitJob, type DocumentInfo, type Source } from "../lib/api";

type Investigation = { investigation_id: string; name: string };
type Finding = {
  type: string; status: string; reason: string | null; currency?: string;
  invoice_amount?: string; purchase_order_amount?: string; difference?: string;
  total_basis?: string; rule_id: string;
  match_basis?: { value: string; vendor_id: string };
  evidence: (Source & { role: string; source_url: string })[];
};
type Detail = Investigation & {
  status: string; job_id: string | null; error_code: string | null;
  sources: { document_id: string }[]; findings: Finding[];
};

const findingLabels: Record<string, string> = {
  AMOUNT_MATCH: "Grand totals match",
  AMOUNT_MISMATCH: "Grand totals differ",
  UNVERIFIED_MATCH: "Match needs review",
  UNVERIFIED_TOTAL: "Total needs review",
};

export default function Comparisons({
  base, documents, openSource,
}: {
  base: string; documents: DocumentInfo[]; openSource: (url: string) => void;
}) {
  const [sources, setSources] = useState<string[]>([]);
  const [items, setItems] = useState<Investigation[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [download, setDownload] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [opened, setOpened] = useState(false);
  const navigation = useRef(0);
  const pendingCreate = useRef<{ scope: string; key: string; id?: string } | null>(null);
  const path = base + "/investigations";
  const eligible = documents.filter((doc) => doc.preview_available);
  const selected = sources.filter((id) => eligible.some((doc) => doc.document_id === id));

  const refresh = useEffectEvent(async (signal: AbortSignal) => {
    const attempt = navigation.current;
    try {
      const next = await list<Investigation>(path, signal);
      if (signal.aborted) return;
      setItems(next);
      if (detail && !busy && attempt === navigation.current) {
        const current = await api<Detail>(path + "/" + detail.investigation_id, { signal });
        if (!signal.aborted && attempt === navigation.current) setDetail(current);
      }
    } catch (reason) {
      if (signal.aborted || attempt !== navigation.current) return;
      if (comparisonInvalid(reason)) {
        setDetail(null);
        setDownload(null);
      }
      setError((reason as Error).message);
    }
  });

  useEffect(() => {
    if (!base || !opened) return;
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [base, documents, opened]);

  async function work(operation: () => Promise<void>) {
    if (busy) return;
    navigation.current += 1;
    setBusy(true);
    setError("");
    setStatus("");
    try {
      await operation();
    } catch (reason) {
      if (comparisonInvalid(reason)) {
        pendingCreate.current = null;
        setDetail(null);
        setDownload(null);
      }
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function compare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selected.length < 2 || selected.length > 3) return;
    void work(async () => {
      setDetail(null);
      setDownload(null);
      const document_ids = [...selected].sort();
      const scope = path + ":" + document_ids.join(",");
      if (pendingCreate.current?.scope !== scope) pendingCreate.current = { scope, key: crypto.randomUUID() };
      const pending = pendingCreate.current;
      if (!pending.id) {
        const receipt = await mutate<Investigation>(path, "POST", { document_ids }, pending.key)
          .catch((reason: unknown) => {
            if (reason instanceof ApiError) throw reason;
            throw new Error("Comparison submission interrupted. Retry with the same PDFs.");
          });
        pending.id = receipt.investigation_id;
      }
      const investigation_id = pending.id;
      const draft = await api<Detail>(path + "/" + investigation_id);
      setDetail(draft);
      setItems((previous) => previous.some((item) => item.investigation_id === investigation_id)
        ? previous : [...previous, { investigation_id, name: draft.name }]);
      await run(investigation_id);
      pendingCreate.current = null;
    });
  }

  async function run(id: string) {
    const target = path + "/" + id;
    const { job_id } = await mutate<{ job_id: string }>(target + "/run", "POST");
    try {
      await waitJob(base, job_id, (job) => setStatus("Comparison · " + job.status));
    } finally {
      const result = await api<Detail>(target);
      setDetail(result);
      if (["failed", "cancelled"].includes(result.status) && pendingCreate.current?.id === id) {
        pendingCreate.current = null;
      }
    }
  }

  function open(id: string) {
    void work(async () => {
      setDetail(null);
      setDownload(null);
      const result = await api<Detail>(path + "/" + id);
      setDetail(result);
      if (result.status === "queued" || result.status === "running") await run(id);
    });
  }

  function exportReport() {
    if (!detail) return;
    void work(async () => {
      setDownload(null);
      const receipt = await mutate<{ job_id: string; report_id: string }>(
        path + "/" + detail.investigation_id + "/reports", "POST", { format: "csv" },
      );
      await waitJob(base, receipt.job_id, (job) => setStatus("Report · " + job.status));
      const report = await api<{ download_url: string }>(base + "/reports/" + receipt.report_id);
      setDownload(report.download_url);
    });
  }

  const visibleDetail = detail?.sources.every((source) =>
    documents.some((doc) => doc.document_id === source.document_id)
  ) ? detail : null;
  const finding = visibleDetail?.findings[0];
  return (
    <details className="panel connections" onToggle={(event) => setOpened(event.currentTarget.open)}>
      <summary><strong>Compare documents</strong><span className="muted">Invoice and purchase order</span></summary>
      <form className="connection-form" onSubmit={compare}>
        <p className="muted">Choose the source PDFs. The comparison uses explicit PO and vendor IDs, currency, and grand-total basis.</p>
        <fieldset className="comparison-sources">
          <legend>Comparison sources</legend>
          {eligible.map((doc) => (
            <label className="scope" key={doc.document_id}>
              <input type="checkbox" checked={selected.includes(doc.document_id)} disabled={busy}
                onChange={(event) => setSources(event.target.checked
                  ? [...sources, doc.document_id] : sources.filter((id) => id !== doc.document_id))} />
              {doc.filename}
            </label>
          ))}
          {!eligible.length && <p className="muted">Upload and extract your source PDFs first.</p>}
        </fieldset>
        <div className="actions">
          <button type="submit" className="primary" disabled={busy || selected.length < 2 || selected.length > 3}>Run comparison</button>
          <span className="muted">Choose two or three PDFs.</span>
        </div>
      </form>
      {!!items.length && <label className="comparison-history">Open previous investigation
        <select disabled={busy} value={visibleDetail?.investigation_id ?? ""} onChange={(event) => {
          if (event.target.value) open(event.target.value);
        }}>
          <option value="">Choose an investigation</option>
          {items.map((item) => <option key={item.investigation_id} value={item.investigation_id}>
            {item.name} · {item.investigation_id.slice(0, 8)}
          </option>)}
        </select>
      </label>}
      {visibleDetail?.status === "draft" && <button type="button" disabled={busy}
        onClick={() => void work(() => run(visibleDetail.investigation_id))}>Run saved investigation</button>}
      {!error && visibleDetail?.error_code && <p role="alert" className="error">{explain(visibleDetail.error_code)}</p>}
      {finding && <article className="turn comparison-result" aria-label="Comparison finding">
        <h3>{findingLabels[finding.type] ?? explain(finding.type)}</h3>
        {finding.reason && <p>{explain(finding.reason)}</p>}
        {finding.status === "verified" && <>
          <p>{finding.currency} {finding.invoice_amount} − {finding.purchase_order_amount} = <strong>{finding.difference}</strong></p>
          <p className="muted">{finding.match_basis?.value} · Vendor {finding.match_basis?.vendor_id} · {finding.total_basis}</p>
        </>}
        <div className="actions">
          {finding.evidence.map((anchor) => (
            <button type="button" className="citation" key={anchor.role} onClick={() => openSource(anchor.source_url)}>
              {anchor.role === "invoice" ? "Invoice" : "Purchase order"} source · page {anchor.page_number}
            </button>
          ))}
          <button type="button" disabled={busy} onClick={exportReport}>Prepare CSV report</button>
          {download && <a href={download}>Download CSV report</a>}
        </div>
      </article>}
      {status && <p role="status" className="muted">{status}</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </details>
  );
}
