const localHost = /^(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?$/i;
const localBackend = new Set(["127.0.0.1", "[::1]"]);
const requestHeaders = ["accept", "content-type", "idempotency-key", "last-event-id"];
const responseHeaders = ["content-type", "content-disposition", "x-accel-buffering", "x-request-id", "link"];
type Context = { params: Promise<{ segments: string[] }> };

function failure(code: string, status: number) {
  return Response.json({ error: { code } }, { status, headers: { "Cache-Control": "no-store" } });
}

async function forward(request: Request, { params }: Context) {
  const host = request.headers.get("host") ?? "";
  const origin = request.headers.get("origin");
  const site = request.headers.get("sec-fetch-site");
  if (!localHost.test(host) || (origin && origin !== "http://" + host) ||
      (site && site !== "same-origin" && site !== "none")) {
    return failure("LOCAL_ORIGIN_REQUIRED", 403);
  }

  const { segments } = await params;
  const isReadiness = segments.length === 1 && segments[0] === "ready" && request.method === "GET";
  if (!segments.every((part) => /^[a-z0-9_-]+$/i.test(part)) ||
      (!isReadiness && segments[0] !== "workspaces")) {
    return failure("NOT_FOUND", 404);
  }

  const token = process.env.DOCUMIND_API_TOKEN;
  if (!token) return failure("LOCAL_API_CONFIGURATION_REQUIRED", 503);
  let target: URL;
  try {
    target = new URL(process.env.DOCUMIND_API_URL ?? "http://127.0.0.1:8000");
    if (target.protocol !== "http:" || !localBackend.has(target.hostname) ||
        target.username || target.password || target.search || target.hash || target.pathname !== "/") {
      return failure("LOCAL_API_CONFIGURATION_REQUIRED", 503);
    }
    target.pathname = "/api/v1/" + segments.join("/");
    target.search = new URL(request.url).search;
  } catch {
    return failure("LOCAL_API_CONFIGURATION_REQUIRED", 503);
  }

  const headers = new Headers({ Authorization: "Bearer " + token });
  for (const name of requestHeaders) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  const options: RequestInit & { duplex?: "half" } = {
    method: request.method, headers, cache: "no-store", redirect: "error", signal: request.signal,
  };
  if (request.body && (request.method === "POST" || request.method === "PATCH")) {
    options.body = request.body;
    options.duplex = "half";
  }
  try {
    const upstream = await fetch(target, options);
    const headers = new Headers({ "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
    for (const name of responseHeaders) {
      const value = upstream.headers.get(name);
      if (value !== null) headers.set(name, value);
    }
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch {
    return failure("LOCAL_API_UNAVAILABLE", 503);
  }
}

export { forward as GET, forward as POST, forward as PATCH, forward as DELETE };
