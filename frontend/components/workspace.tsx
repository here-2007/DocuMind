"use client";

import { useCallback, useEffect, useEffectEvent, useRef, useState, type FormEvent } from "react";
import { api, ApiError, comparisonInvalid, explain, list, mutate, uploadPdf, waitJob, type Citation, type Connection, type DocumentInfo, type Job, type Source, type WorkspaceInfo } from "../lib/api";
import Connections from "./connections";
import Comparisons from "./comparisons";
import QueryChat from "./query-chat";
import SourceViewer from "./source-viewer";

type Activity = { label: string; status: string; progress?: number };

export default function Workspace() {
  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [cleanupJobs, setCleanupJobs] = useState<Job[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [openDocument, setOpenDocument] = useState<string | null>(null);
  const [citation, setCitation] = useState<(Source & { source_url: string }) | null>(null);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmedSource, setConfirmedSource] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [initializing, setInitializing] = useState(true);
  const sourceRequest = useRef(0);
  const uploadKey = useRef<string | null>(null);
  const base = workspace ? "/api/v1/workspaces/" + workspace.id : "";
  const viewed = documents.find((document) => document.document_id === openDocument);
  const reviewSource = viewed?.preview_ingestion_version
    ? viewed.document_id + ":" + viewed.preview_ingestion_version : null;
  const confirmed = !!reviewSource && confirmedSource === reviewSource;
  const searchableSelection = selected.filter((id) => documents.some((doc) => doc.document_id === id && doc.searchable));

  const refresh = useCallback(async (prefix: string, signal?: AbortSignal) => {
    const [docs, endpoints, cleanup] = await Promise.allSettled([
      list<DocumentInfo>(prefix + "/documents", signal), list<Connection>(prefix + "/inference-connections", signal),
      list<Job>(prefix + "/cleanup-jobs", signal),
    ]);
    if (docs.status === "fulfilled") setDocuments(docs.value.sort((a, b) => b.created_at.localeCompare(a.created_at)));
    if (endpoints.status === "fulfilled") setConnections(endpoints.value);
    if (cleanup.status === "fulfilled") setCleanupJobs(cleanup.value);
    if (docs.status === "rejected") throw docs.reason;
    if (endpoints.status === "rejected") throw endpoints.reason;
    if (cleanup.status === "rejected") throw cleanup.reason;
  }, []);

  const refreshConnections = useCallback(async () => {
    if (base) setConnections(await list<Connection>(base + "/inference-connections"));
  }, [base]);

  const revalidateCitation = useEffectEvent(async (signal: AbortSignal) => {
    if (!citation) return;
    const attempt = sourceRequest.current;
    const clear = (code = "SOURCE_UNAVAILABLE") => {
      if (signal.aborted || sourceRequest.current !== attempt) return;
      sourceRequest.current += 1;
      setCitation(null);
      setOpenDocument(null);
      setError(explain(code));
    };
    if (!documents.some((doc) => doc.document_id === citation.document_id)) {
      clear();
      return;
    }
    try {
      await api<Source>(citation.source_url, { signal });
    } catch (error) {
      if (comparisonInvalid(error) || (error instanceof ApiError && error.code === "QUERY_RECHECK_REQUIRED")) {
        clear([404, 410].includes(error.status) ? "SOURCE_UNAVAILABLE" : error.code);
      }
    }
  });

  useEffect(() => {
    const controller = new AbortController();
    void revalidateCitation(controller.signal);
    return () => controller.abort();
  }, [documents]);

  useEffect(() => {
    const controller = new AbortController();
    async function start() {
      try {
        const result = await api<{ items: WorkspaceInfo[] }>("/api/v1/workspaces", { signal: controller.signal });
        const local = result.items[0];
        if (!local) throw new Error("The backend has no initialized workspace.");
        setWorkspace(local);
        await refresh("/api/v1/workspaces/" + local.id, controller.signal);
      } catch (reason) {
        if (!controller.signal.aborted) setError((reason as Error).message);
      } finally {
        if (!controller.signal.aborted) setInitializing(false);
      }
    }
    void start();
    return () => controller.abort();
  }, [refresh]);

  function showDocument(id: string) {
    sourceRequest.current += 1;
    setCitation(null);
    setOpenDocument(id);
    setConfirmedSource(null);
  }

  async function openSource(sourceUrl: string) {
    const attempt = ++sourceRequest.current;
    setError("");
    try {
      const source = await api<Source>(sourceUrl);
      if (attempt !== sourceRequest.current) return;
      setOpenDocument(source.document_id);
      setCitation({ ...source, source_url: sourceUrl });
      setConfirmedSource(null);
      document.getElementById("viewer-title")?.scrollIntoView({ block: "nearest" });
    } catch (reason) {
      if (attempt !== sourceRequest.current) return;
      setCitation(null);
      setOpenDocument(null);
      setError((reason as Error).message);
    }
  }

  async function work(operation: () => Promise<void>) {
    if (!base || busy) return;
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (reason) {
      setActivity((previous) => previous ? { label: previous.label, status: "failed" } : null);
      setError((reason as Error).message);
    } finally {
      try {
        await refresh(base);
      } catch (reason) {
        setError((previous) => previous || (reason as Error).message);
      }
      setBusy(false);
    }
  }

  async function track(jobId: string, label: string) {
    setActivity({ label, status: "queued" });
    await waitJob(base, jobId, (job) => setActivity({ label, status: job.status }));
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const file = new FormData(form).get("file");
    if (!(file instanceof File) || !file.size) return;
    void work(async () => {
      setActivity({ label: file.name, status: "uploading", progress: 0 });
      uploadKey.current ??= crypto.randomUUID();
      const result = await uploadPdf(base + "/documents", file, uploadKey.current, (progress) => {
        setActivity({ label: file.name, status: "uploading", progress });
      });
      uploadKey.current = null;
      form.reset();
      showDocument(result.document_id);
      setSelected((previous) => [...new Set([...previous, result.document_id])]);
      await refresh(base);
      await track(result.job_id, "Extraction · " + file.name);
    });
  }

  function publish() {
    void work(async () => {
      const result = await mutate<{ job_id: string }>(base + "/publications", "POST");
      await track(result.job_id, "Search publication");
    });
  }

  function deleteDocument(doc: DocumentInfo) {
    if (!window.confirm(`Delete ${doc.filename}? Its answers and comparison reports will become unavailable.`)) return;
    void work(async () => {
      const { job_id } = await mutate<{ job_id: string }>(base + "/documents/" + doc.document_id, "DELETE");
      setDocuments((previous) => previous.filter((item) => item.document_id !== doc.document_id));
      setSelected((previous) => previous.filter((id) => id !== doc.document_id));
      await track(job_id, "Deletion · " + doc.filename);
    });
  }

  function retryCleanup(job: Job) {
    void work(async () => {
      await mutate<Job>(base + "/jobs/" + job.id + "/retry", "POST");
      await track(job.id, "Deletion cleanup · " + job.id.slice(0, 8));
    });
  }

  function approve() {
    if (!confirmed || !viewed?.preview_ingestion_version) return;
    void work(async () => {
      const result = await mutate<{ job_id: string }>(base + "/documents/" + viewed.document_id + "/review", "POST", {
        base_ingestion_version: viewed.preview_ingestion_version, confirm_all_pages: true,
      });
      await track(result.job_id, "Source review");
      setConfirmedSource(null);
    });
  }

  return (
    <>
      <header className="topbar">
        <div className="wordmark"><span className="mark" aria-hidden="true">D</span><span>DocuMind</span><small>WORKSPACE</small></div>
        <span className="local"><span aria-hidden="true" /> Local workspace</span>
      </header>
      <main>
        <div className="intro">
          <div><p className="eyebrow">DOCUMENT INTELLIGENCE</p><h1>{workspace?.name ?? "Document workspace"}</h1></div>
          <p>Upload PDFs, ask grounded questions, and open the source behind each answer.</p>
        </div>
        {initializing && <p role="status">Connecting to the local workspace…</p>}
        {error && <p className="error workspace-error" role="alert">{error}</p>}
        <div className="workspace-grid">
          <aside className="panel library" aria-labelledby="documents-title">
            <div className="panel-head"><div><p className="eyebrow">LIBRARY</p><h2 id="documents-title">Documents</h2></div><span className="badge">{documents.length}</span></div>
            <form className="upload-form" onSubmit={upload}>
              <label htmlFor="pdf-file">PDF file</label>
              <input id="pdf-file" name="file" type="file" accept="application/pdf,.pdf" required disabled={busy}
                onChange={() => { uploadKey.current = null; }} />
              <button className="primary" type="submit" disabled={!base || busy}>Upload PDF</button>
            </form>
            <div className="actions library-actions">
              <button type="button" disabled={!base || busy || !documents.length} onClick={publish}>Publish search index</button>
              <button type="button" disabled={!base || busy} onClick={() => void work(async () => setActivity(null))}>Refresh</button>
            </div>
            {activity && <div className="activity" role="status">
              <strong>{activity.label}</strong><span>{activity.status}{activity.progress !== undefined ? " · " + activity.progress + "%" : ""}</span>
              {activity.progress !== undefined && <progress value={activity.progress} max={100} aria-label="Upload progress" />}
            </div>}
            {cleanupJobs.length > 0 && <ul aria-label="Deletion cleanup">
              {cleanupJobs.map((job) => <li key={job.id}>
                <span>Cleanup {job.id.slice(0, 8)} · {job.status}</span>
                {job.error_code && <p className="error">{explain(job.error_code)}</p>}
                {job.status === "failed" && <button type="button" disabled={busy}
                  onClick={() => retryCleanup(job)}>Retry cleanup</button>}
              </li>)}
            </ul>}
            <ul className="documents">
              {documents.map((doc) => <li key={doc.document_id} className={openDocument === doc.document_id ? "current" : ""}>
                <button type="button" className="file-link" onClick={() => showDocument(doc.document_id)}>{doc.filename}</button>
                <div className="document-states">
                  <span className="badge">{doc.extraction_status.replaceAll("_", " ")}</span>
                  <span className={doc.searchable ? "badge searchable" : "badge"}>{doc.searchable ? "Searchable" : "Unpublished"}</span>
                </div>
                {doc.searchable && doc.published_ingestion_version !== doc.latest_ingestion_version && <small className="muted">Search uses a previous extraction.</small>}
                {doc.error_code && <p className="error">{explain(doc.error_code)}</p>}
                <label className="scope"><input type="checkbox" disabled={!doc.searchable} checked={doc.searchable && selected.includes(doc.document_id)}
                  onChange={(event) => setSelected(event.target.checked ? [...selected, doc.document_id] : selected.filter((id) => id !== doc.document_id))} />
                  Include in chat
                </label>
              </li>)}
            </ul>
            {!documents.length && <p className="muted">Your PDFs will appear here.</p>}
          </aside>
          <QueryChat base={base} selected={searchableSelection} connections={connections} documents={documents}
            openCitation={(value: Citation) => void openSource(base + "/citations/" + value.citation_id)}
            refreshConnections={refreshConnections} />
          <div className="source-column">
            <SourceViewer base={base} document={viewed} citation={citation}
              onDelete={viewed && !busy ? () => deleteDocument(viewed) : undefined}
              key={citation?.source_url ?? (viewed?.document_id ?? "") + (viewed?.preview_ingestion_version ?? "")} />
            {viewed?.extraction_status === "needs_review" && viewed.preview_available && !citation && (
              <section className="panel review" aria-label="Extraction review">
                <p className="muted">This extraction needs human review before its new version can be published.</p>
                <label className="scope"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmedSource(event.target.checked ? reviewSource : null)} /> I reviewed every page.</label>
                <button type="button" disabled={!confirmed || busy} onClick={approve}>Confirm source review</button>
              </section>
            )}
          </div>
        </div>
        <Comparisons base={base} documents={documents} openSource={(url) => void openSource(url)} />
        <Connections base={base} items={connections} refresh={refreshConnections} />
      </main>
    </>
  );
}
