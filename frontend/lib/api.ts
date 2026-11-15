export type Collection<T> = { items: T[]; next_cursor: string | null };
export type WorkspaceInfo = { id: string; name: string };
export type DocumentInfo = {
  document_id: string; filename: string; created_at: string; latest_ingestion_version: string;
  preview_ingestion_version: string | null; published_ingestion_version: string | null;
  extraction_status: string; index_status: string; error_code: string | null;
  preview_available: boolean; searchable: boolean;
};
export type Job = { id: string; status: string; error_code: string | null };
export type Citation = {
  citation_id: string; document_id: string; page_number: number; element_id: string;
  chunk_id: string; ingestion_version: string; quote: string;
};
export type Source = Pick<Citation, "document_id" | "ingestion_version" | "page_number" | "quote"> & {
  element_id: string; bbox_norm: [number, number, number, number] | null;
};
export type PageData = { page_number: number; page_count: number; image_url: string; text_raw: string };
export type Connection = {
  id: string; display_name: string; endpoint_url: string; model_id: string;
  config_version: number; status: string; supported_modes: string[]; error_code: string | null;
};
export type Receipt = { query_run_id: string; events_url: string; result_url: string };
export type Run = {
  query_run_id: string; status: string; mode: string; answer?: string;
  grounding_status?: string; citations?: Citation[]; error: { code: string } | null;
  inference?: { model_id: string; config_version?: number; candidate_accepted?: boolean;
    strategy?: string; generator_connection_id?: string; generator_config_version?: number;
    generator_model_id?: string } | null;
};
export type Trace = { seq: number; type: string; data: Record<string, unknown> };

export function explain(code: string) {
  const messages: Record<string, string> = {
    LOCAL_API_CONFIGURATION_REQUIRED: "Configure the workspace server with the local API token.",
    LOCAL_API_UNAVAILABLE: "The local API is unavailable. Check that FastAPI is running.",
    UNAUTHENTICATED: "The workspace server could not authenticate to the local API.",
    CONNECTION_VERSION_CONFLICT: "The connection changed. Refresh it and choose the destination again.",
    CONNECTION_UNAVAILABLE: "The selected endpoint is unavailable. Select a ready connection for a new run.",
    INFERENCE_QUOTA_EXCEEDED: "The selected endpoint has reached its quota. Choose a connection for a new run.",
    GENERATOR_BUSY: "The selected generator is busy. Try the question again when it is available.",
    SOURCE_UNAVAILABLE: "The source is no longer available.",
    SOURCE_ARTIFACT_UNAVAILABLE: "A stored source file is missing or unreadable. Restore the matching document files.",
    SOURCE_HASH_MISMATCH: "The stored source does not match its recorded contents. Restore the matching document files.",
    WORKSPACE_NOT_INITIALIZED: "Initialize the local workspace before uploading documents.",
    DATABASE_MIGRATION_REQUIRED: "Update the local database with uv run --no-sync alembic upgrade head, then restart the app.",
    INDEX_NOT_READY: "Publish a searchable snapshot before asking questions.",
    SEARCH_DISABLED: "Enable local indexing in the backend to publish and search documents.",
    GENERATION_DISABLED: "Custom endpoint generation is disabled in the local backend.",
    DOCUMENT_NOT_READY: "The selected document is not ready for this operation.",
    FILE_TOO_LARGE: "This PDF exceeds the configured upload size limit.",
    WORKSPACE_DOCUMENT_LIMIT: "The workspace is full. Delete an unused PDF and wait for cleanup before uploading again.",
    COMPARISON_TABLE_FIELDS_UNSUPPORTED: "A table contains a comparison field that needs review.",
    INVESTIGATION_RECHECK_REQUIRED: "A table contains a comparison field. Start a new investigation to recheck these sources.",
    QUERY_CANCELLED: "Query cancelled.",
    QUERY_RECHECK_REQUIRED: "This answer needs a new evidence check. Start a new query.",
  };
  return messages[code] ?? code.toLowerCase().replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}

export class ApiError extends Error {
  constructor(public readonly code: string, public readonly status: number) {
    super(explain(code));
  }
}

export function comparisonInvalid(error: unknown): error is ApiError {
  return error instanceof ApiError && ([404, 410].includes(error.status) || [
    "INVESTIGATION_RECHECK_REQUIRED", "COMPARISON_INTEGRITY_ERROR", "INVALID_SOURCE_SPAN",
  ].includes(error.code));
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...options, cache: "no-store" });
  if (response.status === 204) return undefined as T;
  const data = await response.json();
  if (!response.ok) throw new ApiError(data.error?.code ?? "REQUEST_FAILED", response.status);
  return data as T;
}

export function mutate<T>(path: string, method: "POST" | "PATCH" | "DELETE", body?: object, key = crypto.randomUUID()) {
  return api<T>(path, {
    method,
    headers: { "Idempotency-Key": key, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
}

export async function list<T>(path: string, signal?: AbortSignal) {
  const items: T[] = [];
  let cursor: string | null = null;
  do {
    const page: Collection<T> = await api(path + (cursor ? "?cursor=" + encodeURIComponent(cursor) : ""), { signal });
    items.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor);
  return items;
}

export function terminal(status: string) {
  return ["succeeded", "failed", "cancelled"].includes(status);
}

export async function waitJob(base: string, jobId: string, onStatus: (job: Job) => void) {
  const deadline = Date.now() + 10 * 60_000;
  while (Date.now() < deadline) {
    const job = await api<Job>(base + "/jobs/" + jobId);
    onStatus(job);
    if (terminal(job.status)) {
      if (job.status !== "succeeded") throw new ApiError(job.error_code ?? "JOB_FAILED", 409);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 750));
  }
  throw new Error("The job is still running. Refresh the workspace to check its progress.");
}

export function uploadPdf(path: string, file: File, key: string, progress: (percent: number) => void) {
  return new Promise<{ job_id: string; document_id: string }>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.responseType = "json";
    xhr.timeout = 60_000;
    xhr.setRequestHeader("Idempotency-Key", key);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) progress(Math.round(100 * event.loaded / event.total));
    };
    xhr.onload = () => {
      if (xhr.status === 202 && xhr.response?.job_id) resolve(xhr.response);
      else reject(new ApiError(xhr.response?.error?.code ?? "UPLOAD_FAILED", xhr.status));
    };
    xhr.onerror = xhr.ontimeout = () => reject(new Error("Upload interrupted. Check the local API."));
    const body = new FormData();
    body.set("file", file);
    xhr.send(body);
  });
}
