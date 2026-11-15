import { api, ApiError, terminal, type Receipt, type Run, type Trace } from "./api";

// The server uses UTF-8 SSE frames. Keep partial lines across network chunks.
async function* frames(response: Response) {
  if (!response.body) return;
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) return;
      buffer += chunk.value;
      let boundary = /\r?\n\r?\n/.exec(buffer);
      while (boundary) {
        const lines = buffer.slice(0, boundary.index).split(/\r?\n/);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const value = (name: string) => lines.filter((line) => line.startsWith(name + ":"))
          .map((line) => line.slice(name.length + 1).replace(/^ /, "")).join("\n");
        const data = value("data");
        if (data) yield { kind: value("event"), seq: Number(value("id")), data: JSON.parse(data) };
        boundary = /\r?\n\r?\n/.exec(buffer);
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function follow(
  receipt: Receipt,
  signal: AbortSignal,
  onTrace: (trace: Trace) => void,
  onState: (state: string) => void,
): Promise<Run> {
  let cursor = 0;
  let attempts = 0;
  while (!signal.aborted && attempts < 10) {
    try {
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      if (cursor) headers["Last-Event-ID"] = String(cursor);
      const stream = await fetch(receipt.events_url, { headers, signal, cache: "no-store" });
      if (!stream.ok) {
        const error = await stream.json();
        if (error.error?.code === "EVENTS_EXPIRED") return await api<Run>(receipt.result_url, { signal });
        throw new ApiError(error.error?.code ?? "TRACE_UNAVAILABLE", stream.status);
      }
      if (stream.status === 204) {
        const run = await api<Run>(receipt.result_url, { signal });
        if (terminal(run.status)) return run;
      } else {
        onState("Live trace");
        for await (const frame of frames(stream)) {
          if (frame.kind === "stream_error") {
            if (frame.data.code === "EVENTS_EXPIRED") return await api<Run>(receipt.result_url, { signal });
            throw new ApiError(frame.data.code, 410);
          }
          if (!Number.isSafeInteger(frame.seq) || frame.seq <= cursor) continue;
          cursor = frame.seq;
          attempts = 0;
          const finished = frame.kind === "answer" || frame.kind === "error";
          onTrace({ seq: cursor, type: finished ? frame.kind : frame.data.type, data: frame.data.data ?? frame.data });
          if (finished) return await api<Run>(receipt.result_url, { signal });
        }
      }
    } catch (error) {
      if (signal.aborted || (error instanceof ApiError && error.status < 500)) throw error;
    }
    onState("Reconnecting trace");
    attempts += 1;
    await new Promise((resolve) => setTimeout(resolve, Math.min(250 * attempts, 1_500)));
  }
  throw new Error("Trace disconnected. Open the run again to resume it.");
}
