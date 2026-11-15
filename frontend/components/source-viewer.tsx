"use client";

import { useEffect, useState } from "react";
import { api, type DocumentInfo, type PageData, type Source } from "../lib/api";

export default function SourceViewer({
  base, document, citation, onDelete,
}: { base: string; document: DocumentInfo | undefined; citation: Source | null; onDelete?: () => void }) {
  const [number, setNumber] = useState(citation?.page_number ?? 1);
  const [page, setPage] = useState<PageData | null>(null);
  const [error, setError] = useState("");
  const documentId = citation?.document_id ?? document?.document_id;
  const version = citation?.ingestion_version ?? document?.preview_ingestion_version;
  const sourcePath = base + "/documents/" + documentId;

  useEffect(() => {
    if (!documentId || !version) return;
    const controller = new AbortController();
    api<PageData>(sourcePath + "/pages/" + number + "?ingestion_version=" + version, {
      signal: controller.signal,
    }).then((data) => {
      setError("");
      setPage(data);
    }).catch((reason: Error) => {
      if (!controller.signal.aborted) setError(reason.message);
    });
    return () => controller.abort();
  }, [documentId, version, sourcePath, number]);

  const current = page?.page_number === number ? page : null;
  const box = number === citation?.page_number ? citation.bbox_norm : null;
  return (
    <section className="panel viewer" aria-labelledby="viewer-title">
      <div className="panel-head">
        <div><p className="eyebrow">SOURCE INSPECTOR</p><h2 id="viewer-title">Document preview</h2></div>
        <div className="actions">
          {version && <a className="text-link" href={sourcePath + "/original"}>Download original</a>}
          {document && <button type="button" disabled={!onDelete} onClick={onDelete}>Delete PDF</button>}
        </div>
      </div>
      {documentId && <p className="muted file-title">{document?.filename ?? "Cited document"}</p>}
      {citation && (
        <div className="source-quote">
          <span className="eyebrow">CITED EXCERPT · PAGE {citation.page_number}</span>
          <blockquote>{citation.quote}</blockquote>
        </div>
      )}
      {!version && <div className="empty">
        {documentId ? "The preview will appear after extraction." : "Open a document or citation to see its source page."}
      </div>}
      {error && <p role="alert" className="error">{error}</p>}
      {version && !error && !current && <p role="status" className="muted">Loading page {number}…</p>}
      {current && (
        <>
          <nav className="page-controls" aria-label="Source page navigation">
            <button type="button" disabled={number <= 1} onClick={() => setNumber(number - 1)}>Previous</button>
            <span>Page {number} of {current.page_count}</span>
            <button type="button" disabled={number >= current.page_count} onClick={() => setNumber(number + 1)}>Next</button>
          </nav>
          <div className="paper-scroll">
            <div className="paper">
              {/* Keep protected PNGs on the browser's same-origin API path. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={current.image_url} alt={"Original PDF page " + number} onError={() => setError("The source page image is unavailable. Restore the matching stored files and refresh.")} />
              {box && <div className="highlight" data-testid="citation-highlight" aria-label="Cited source region" style={{
                left: (box[0] * 100) + "%", top: (box[1] * 100) + "%",
                width: ((box[2] - box[0]) * 100) + "%", height: ((box[3] - box[1]) * 100) + "%",
              }} />}
            </div>
          </div>
          {citation && number === citation.page_number && !box && <p className="muted">This citation has an exact excerpt and no page geometry.</p>}
          <details><summary>Extracted page text</summary><pre>{current.text_raw}</pre></details>
        </>
      )}
    </section>
  );
}
