import { expect, test } from "@playwright/test";

test("uploads, publishes, replays trace, and opens the cited PDF page", async ({ page, request }) => {
  const pdf = process.env.DOCUMIND_TEST_PDF;
  const endpoint = process.env.DOCUMIND_TEST_CHAT_URL;
  if (!pdf || !endpoint) throw new Error("Run the browser test through the isolated backend harness.");

  const crossOrigin = await request.get("/api/v1/workspaces", {
    headers: { Origin: "http://untrusted.example", "Sec-Fetch-Site": "cross-site" },
  });
  expect(crossOrigin.status()).toBe(403);
  const browserAuth: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/api/v1/") && req.headers().authorization) browserAuth.push(req.url());
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Local workspace" })).toBeVisible();
  const uploads: string[] = [];
  await page.route("**/workspaces/*/documents", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    uploads.push(route.request().headers()["idempotency-key"]);
    if (uploads.length === 1) {
      const accepted = await request.post(route.request().url(), {
        headers: { "Idempotency-Key": uploads[0] }, multipart: { file: (await import("node:fs")).createReadStream(pdf) },
      });
      expect(accepted.status(), await accepted.text()).toBe(202);
      await route.abort("connectionreset");
      return;
    }
    await route.continue();
  });
  await page.getByLabel("PDF file").setInputFiles(pdf);
  await page.getByRole("button", { name: "Upload PDF" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Upload interrupted" })).toBeVisible();
  await page.getByRole("button", { name: "Upload PDF" }).click();
  const library = page.getByRole("complementary", { name: "Documents" });
  await expect(library.getByText("complete", { exact: true })).toBeVisible({ timeout: 35_000 });
  expect(uploads).toHaveLength(2);
  expect(uploads[0]).toBe(uploads[1]);
  await expect(library.getByRole("button", { name: "browser-invoice.pdf" })).toHaveCount(1);
  await page.unroute("**/workspaces/*/documents");
  await expect(library.getByText("Unpublished", { exact: true })).toBeVisible();
  await expect(page.getByText("Page 1 of 2")).toBeVisible();
  await page.getByRole("button", { name: "Publish search index" }).click();
  await expect(library.getByText("Searchable", { exact: true })).toBeVisible({ timeout: 45_000 });

  await page.getByText("Inference connections", { exact: true }).click();
  await page.getByLabel("Connection name").fill("Local test endpoint");
  await page.getByLabel("Chat completions URL").fill(endpoint);
  await page.getByLabel("Model ID").fill("phase2-test");
  await page.getByLabel("API key").fill("synthetic-token");
  const saves: { key: string; body: string; id: string; version: number }[] = [];
  let recoveryDenied = false;
  await page.route("**/inference-connections{,/*}", async (route) => {
    const incoming = route.request();
    if (!["POST", "PATCH"].includes(incoming.method()) || incoming.url().endsWith("/validate")) return route.continue();
    if (saves.length === 1 && !recoveryDenied) {
      recoveryDenied = true;
      return route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ error: { code: "UNAUTHENTICATED" } }) });
    }
    const accepted = await route.fetch();
    expect(accepted.ok(), await accepted.text()).toBeTruthy();
    const item = await accepted.json();
    saves.push({ key: incoming.headers()["idempotency-key"], body: incoming.postData() ?? "", id: item.id, version: item.config_version });
    if (saves.length % 2 === 1) return route.abort("connectionreset");
    await route.fulfill({ response: accepted });
  });
  await page.getByRole("button", { name: "Save connection" }).click();
  await expect(page.getByText("Save not confirmed.", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry save" })).toBeEnabled();
  await expect(page.getByLabel("Connection name")).toBeDisabled();
  await expect(page.getByLabel("API key")).toHaveValue("");
  await page.getByRole("button", { name: "Retry save" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "could not authenticate" })).toBeVisible();
  await expect(page.getByLabel("Connection name")).toBeDisabled();
  await page.getByRole("button", { name: "Retry save" }).click();
  await expect(page.getByText("Connection saved. Validate it before use.")).toBeVisible();
  expect(saves[0]).toEqual(saves[1]);
  await expect(page.locator(".connection-list li")).toHaveCount(1);
  await page.getByRole("button", { name: "Replace credential or edit" }).click();
  await page.getByLabel("API key").fill("synthetic-token");
  await page.getByRole("button", { name: "Save new version" }).click();
  await expect(page.getByRole("button", { name: "Retry save" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Cancel edit" })).toBeDisabled();
  await page.getByRole("button", { name: "Retry save" }).click();
  await expect(page.getByText("Connection saved. Validate it before use.")).toBeVisible();
  expect(saves).toHaveLength(4);
  expect(saves[2]).toEqual(saves[3]);
  expect(saves[2].id).toBe(saves[0].id);
  expect(saves[2].version).toBe(saves[0].version + 1);
  await expect(page.locator(".connection-list li")).toHaveCount(1);
  await page.unroute("**/inference-connections{,/*}");
  await page.getByRole("button", { name: "Validate endpoint" }).click();
  await expect(page.getByText("Endpoint validated for baseline RAG.")).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Generation").selectOption({ label: "Local test endpoint" });
  await expect(page.getByText("Sends the question and verified excerpt to " + endpoint)).toBeVisible();

  let disconnected = false;
  let resumed = false;
  await page.route("**/queries/*/events", async (route) => {
    const incoming = route.request();
    if (!disconnected) {
      disconnected = true;
      const stored = await request.get(incoming.url());
      const { items: [first] } = await stored.json();
      await expect.poll(async () => {
        const result = await request.get(incoming.url().replace(/\/events$/, ""));
        return (await result.json()).status;
      }, { timeout: 30_000 }).toBe("succeeded");
      const data = JSON.stringify({ seq: first.seq, type: first.event_type, data: first.payload });
      const frame = "id: " + first.seq + "\nevent: trace_event\ndata: " + data + "\n\n";
      await route.fulfill({ contentType: "text/event-stream", body: frame + frame });
      return;
    }
    resumed ||= incoming.headers()["last-event-id"] === "1";
    await route.continue();
  });
  await page.getByLabel("Question").fill("What is the invoice grand total?");
  const queryKeys: string[] = [];
  const queryBodies: string[] = [];
  const queryIds: string[] = [];
  await page.route("**/workspaces/*/queries", async (route) => {
    const incoming = route.request();
    if (incoming.method() !== "POST") return route.continue();
    queryKeys.push(incoming.headers()["idempotency-key"]);
    queryBodies.push(incoming.postData() ?? "");
    const accepted = await route.fetch();
    expect(accepted.status()).toBe(202);
    queryIds.push((await accepted.json()).query_run_id);
    if (queryKeys.length === 1) return route.abort("connectionreset");
    await route.fulfill({ response: accepted });
  });
  await page.getByRole("button", { name: "Ask question" }).click();
  await expect(page.getByText("Submission not confirmed.", { exact: false })).toBeVisible();
  await expect(page.getByLabel("Question")).toBeDisabled();
  await expect(page.getByLabel("Generation")).toBeDisabled();
  await page.getByRole("button", { name: "Retry query", exact: true }).click();
  await expect(page.getByText("VERIFIED ANSWER", { exact: true })).toBeVisible({ timeout: 45_000 });
  expect(queryKeys).toHaveLength(2);
  expect(queryKeys[0]).toBe(queryKeys[1]);
  expect(queryBodies[0]).toBe(queryBodies[1]);
  expect(queryIds[0]).toBe(queryIds[1]);
  await page.unroute("**/workspaces/*/queries");
  expect(resumed).toBe(true);
  await page.getByText(/Run trace ·/).first().click();
  await expect(page.getByText("policy decision", { exact: true })).toHaveCount(1);
  await expect(page.getByText("passage found", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("generation started", { exact: true })).toBeVisible();
  await expect(page.getByText("reflection token", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Open citation · page 2" }).click();
  await expect(page.getByText("Page 2 of 2")).toBeVisible();
  await expect(page.getByTestId("citation-highlight")).toBeVisible();
  await expect.poll(() => page.getByAltText("Original PDF page 2").evaluate((element) =>
    (element as HTMLImageElement).naturalWidth,
  )).toBeGreaterThan(0);
  await expect(page.locator("blockquote")).toHaveText("Grand total: INR 1,100.00");
  const screenshot = process.env.DOCUMIND_TEST_SCREENSHOT;
  if (screenshot) await page.screenshot({ path: screenshot, fullPage: true });

  await page.getByLabel("Question").fill("Was invoice INV-830 approved?");
  await page.getByRole("button", { name: "Ask question" }).click();
  await expect(page.getByText("INSUFFICIENT EVIDENCE", { exact: true })).toBeVisible();
  const abstention = page.getByRole("article").filter({ hasText: "Was invoice INV-830 approved?" });
  await expect(abstention.getByText("Selected endpoint", { exact: false })).toBeVisible();
  await expect(abstention.getByText("Endpoint used for this run", { exact: false })).toHaveCount(0);

  await page.getByLabel("Question").fill("What is the grand total?");
  await page.getByRole("button", { name: "Ask question" }).click();
  await page.getByRole("button", { name: "Cancel query" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Query cancelled." })).toBeVisible({ timeout: 30_000 });

  await page.getByLabel("Question").fill("What is the full billed amount?");
  await page.getByRole("button", { name: "Ask question" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "has reached its quota" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("option", { name: "Selected endpoint unavailable" })).toHaveCount(1);
  await expect(page.getByRole("option", { name: "Local test endpoint" })).toHaveCount(0);

  await page.getByRole("button", { name: "Replace credential or edit" }).click();
  await expect(page.getByLabel("API key")).toHaveValue("");
  await page.getByLabel("API key").fill("rotated-token");
  await page.getByRole("button", { name: "Save new version" }).click();
  await expect(page.getByText("phase2-test · v2 · No validated mode")).toBeVisible();
  await expect(page.getByRole("option", { name: "Selected endpoint unavailable" })).toHaveCount(1);
  await page.getByRole("button", { name: "Revoke" }).click();
  await expect(page.getByText("revoked", { exact: true })).toBeVisible();
  expect(browserAuth).toEqual([]);
  await page.getByLabel("PDF file").setInputFiles({
    name: "broken.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.7\nbroken"),
  });
  await page.getByRole("button", { name: "Upload PDF" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Unprocessable document" }).first()).toBeVisible({ timeout: 35_000 });
  await expect(library.getByRole("listitem").filter({ hasText: "broken.pdf" }).getByText("failed", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Open citation · page 2" }).click();
  await expect(page.getByTestId("citation-highlight")).toBeVisible();
  const workspaces = await (await request.get("/api/v1/workspaces")).json();
  const docsPath = "/api/v1/workspaces/" + workspaces.items[0].id + "/documents";
  const docs = await (await request.get(docsPath)).json();
  const invoice = docs.items.find((doc: { filename: string }) => doc.filename === "browser-invoice.pdf");
  expect((await request.delete(docsPath + "/" + invoice.document_id)).status()).toBe(202);
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(library.getByRole("button", { name: "browser-invoice.pdf" })).toHaveCount(0);
  await expect(page.getByText("VERIFIED ANSWER", { exact: true })).toHaveCount(0);
  await expect(page.locator("blockquote")).toHaveCount(0);
  await expect(page.getByTestId("citation-highlight")).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  if (screenshot) await page.screenshot({ path: screenshot.replace(".png", "-mobile.png"), fullPage: true });
});

test("review confirmation is scoped to the exact preview version", async ({ page }) => {
  const id = "6c3e92cd-615a-4a75-8b12-a4707665d923";
  let version = "a".repeat(64);
  await page.route("**/workspaces/*/documents", (route) => route.fulfill({ json: {
    items: [{
      document_id: id, filename: "needs-review.pdf", created_at: "2026-11-09",
      latest_ingestion_version: version, preview_ingestion_version: version,
      extraction_status: "needs_review", preview_available: true, searchable: false,
    }], next_cursor: null,
  } }));
  await page.route("**/documents/" + id + "/pages/1?*", (route) => route.fulfill({ json: {
    page_number: 1, page_count: 1, image_url: "data:image/png;base64,AA==", text_raw: version,
  } }));
  await page.goto("/");
  await page.getByRole("button", { name: "needs-review.pdf" }).click();
  const reviewed = page.getByLabel("I reviewed every page.");
  await reviewed.check();
  await expect(page.getByRole("button", { name: "Confirm source review" })).toBeEnabled();
  version = "b".repeat(64);
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(reviewed).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Confirm source review" })).toBeDisabled();
});

test("clears a cached answer when its evidence requires rechecking", async ({ page }) => {
  const doc = "6c3e92cd-615a-4a75-8b12-a4707665d923";
  const query = "ab1f94fb-1ae8-4127-91bd-83654a8d1d87";
  let recheck = false;
  await page.route("**/workspaces/*/documents", (route) => route.fulfill({ json: {
    items: [{ document_id: doc, filename: "cached.pdf", created_at: "2026-11-09",
      extraction_status: "complete", preview_available: true, searchable: true,
      preview_ingestion_version: "a".repeat(64), published_ingestion_version: "a".repeat(64) }], next_cursor: null,
  } }));
  await page.route("**/workspaces/*/queries", (route) => route.fulfill({ status: 202, json: {
    query_run_id: query, result_url: "/api/v1/evaluation/" + query, events_url: "/api/v1/evaluation/" + query + "/events",
  } }));
  await page.route("**/evaluation/" + query + "/events", (route) => route.fulfill({
    contentType: "text/event-stream", body: 'id: 1\nevent: answer\ndata: {"seq":1}\n\n',
  }));
  await page.route("**/evaluation/" + query, (route) => route.fulfill(recheck ? {
    status: 409, json: { error: { code: "QUERY_RECHECK_REQUIRED" } },
  } : { json: { query_run_id: query, status: "succeeded", mode: "baseline_rag",
    answer: "Grand total: INR 10.00", grounding_status: "supported", citations: [], error: null } }));
  await page.goto("/");
  await page.getByLabel("Include in chat").check();
  await page.getByLabel("Question").fill("What is the total?");
  await page.getByRole("button", { name: "Ask question" }).click();
  await expect(page.getByText("VERIFIED ANSWER", { exact: true })).toBeVisible();
  recheck = true;
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByText("VERIFIED ANSWER", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Grand total: INR 10.00", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("alert").filter({ hasText: "needs a new evidence check" })).toBeVisible();
});

test("compares two extracted PDFs and exports their cited difference", async ({ page, request }) => {
  const invoice = process.env.DOCUMIND_TEST_COMPARISON_INVOICE;
  const order = process.env.DOCUMIND_TEST_COMPARISON_ORDER;
  if (!invoice || !order) throw new Error("Run through the isolated backend harness.");
  const comparisonOnly = process.env.DOCUMIND_TEST_COMPARISON_ONLY === "1";
  if (comparisonOnly) {
    const ready = await (await request.get("/api/v1/ready")).json();
    expect(ready).toMatchObject({ native_ingestion: true, search: false, generation: false, self_rag: false });
  }
  await page.goto("/");
  for (const [file, name] of [[invoice, "comparison-invoice.pdf"], [order, "purchase-order.pdf"]]) {
    await page.getByLabel("PDF file").setInputFiles(file);
    await page.getByRole("button", { name: "Upload PDF" }).click();
    const document = page.getByRole("complementary", { name: "Documents" })
      .getByRole("listitem").filter({ hasText: name });
    await expect(document.getByText("complete", { exact: true })).toBeVisible({ timeout: 35_000 });
    await expect(document.getByText("Unpublished", { exact: true })).toBeVisible();
  }
  if (comparisonOnly) {
    await page.getByLabel("PDF file").setInputFiles(invoice);
    await page.getByRole("button", { name: "Upload PDF" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "The workspace is full" })).toBeVisible();
  }
  await page.getByText("Compare documents", { exact: true }).click();
  const sources = page.getByRole("group", { name: "Comparison sources" });
  await sources.getByRole("checkbox", { name: "comparison-invoice.pdf" }).check();
  await sources.getByRole("checkbox", { name: "purchase-order.pdf" }).check();
  const creates: { key: string; body: string | null }[] = [];
  let acceptedId = "";
  await page.route("**/investigations", async (route) => {
    const req = route.request();
    if (req.method() !== "POST") return route.continue();
    creates.push({ key: req.headers()["idempotency-key"], body: req.postData() });
    if (creates.length !== 1) return route.continue();
    const accepted = await route.fetch();
    expect(accepted.status()).toBe(201);
    acceptedId = (await accepted.json()).investigation_id;
    await route.abort("connectionreset");
  });
  const run = page.getByRole("button", { name: "Run comparison", exact: true });
  await run.click();
  await expect(page.getByRole("alert").filter({ hasText: "Retry with the same PDFs" })).toBeVisible();
  const draftUrl = "**/investigations/" + acceptedId;
  await page.route(draftUrl, (route) => route.fulfill({
    status: 503, json: { error: { code: "LOCAL_API_UNAVAILABLE" } },
  }), { times: 1 });
  await run.click();
  await expect(page.getByRole("alert").filter({ hasText: "The local API is unavailable" })).toBeVisible();
  await run.click();
  const finding = page.getByRole("article", { name: "Comparison finding" });
  await expect(finding.getByText("Grand totals differ")).toBeVisible({ timeout: 30_000 });
  await expect(finding.getByText("INR 1100.00 − 1000.00 = 100.00")).toBeVisible();
  expect(creates).toHaveLength(2);
  expect(creates[1]).toEqual(creates[0]);
  await page.unroute("**/investigations");
  await finding.getByRole("button", { name: "Invoice source · page 1" }).click();
  await expect(page.locator("blockquote")).toHaveText("Grand total: INR 1,100.00");
  await expect(page.getByTestId("citation-highlight")).toBeVisible();
  await finding.getByRole("button", { name: "Purchase order source · page 1" }).click();
  await expect(page.locator("blockquote")).toHaveText("Grand total: INR 1,000.00");
  await finding.getByRole("button", { name: "Prepare CSV report" }).click();
  const download = finding.getByRole("link", { name: "Download CSV report" });
  await expect(download).toBeVisible({ timeout: 30_000 });
  const response = await request.get((await download.getAttribute("href"))!);
  expect(response.ok()).toBe(true);
  expect(await response.text()).toContain("1100.00,1000.00,100.00");
  const [workspace] = (await (await request.get("/api/v1/workspaces")).json()).items;
  const base = "/api/v1/workspaces/" + workspace.id;
  const history = await (await request.get(base + "/investigations")).json();
  expect(history.items.map((item: { investigation_id: string }) => item.investigation_id)).toEqual([acceptedId]);
  const path = base + "/investigations/" + acceptedId;
  const stale = "**" + path + "**";
  await page.route(stale, (route) => route.fulfill({ status: 409, json: { error: { code: "INVESTIGATION_RECHECK_REQUIRED" } } }));
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(finding).toHaveCount(0);
  await expect(page.getByTestId("citation-highlight")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Download CSV report" })).toHaveCount(0);
  await page.unroute(stale);
  await page.getByLabel("Open previous investigation").selectOption(acceptedId);
  await expect(finding.getByText("INR 1100.00 − 1000.00 = 100.00")).toBeVisible();
  await finding.getByRole("button", { name: "Invoice source · page 1" }).click();
  await expect(page.getByTestId("citation-highlight")).toBeVisible();
  const deletePdf = page.getByRole("button", { name: "Delete PDF", exact: true });
  page.once("dialog", (dialog) => dialog.dismiss());
  await deletePdf.click();
  await expect(finding).toBeVisible();
  expect((await request.get(response.url())).status()).toBe(200);
  page.once("dialog", (dialog) => dialog.accept());
  await deletePdf.click();
  await expect(finding).toHaveCount(0);
  await expect(page.getByTestId("citation-highlight")).toHaveCount(0);
  expect((await request.get(response.url())).status()).toBe(410);
  const library = page.getByRole("complementary", { name: "Documents" });
  if (comparisonOnly) {
    const retry = library.getByRole("button", { name: "Retry cleanup", exact: true });
    await expect(retry).toBeVisible();
    await page.reload();
    await expect(retry).toBeVisible();
    await retry.click();
    await expect(library.getByRole("status").filter({ hasText: "Deletion cleanup" })).toContainText("succeeded");
    await expect(retry).toHaveCount(0);
    await page.getByLabel("PDF file").setInputFiles(invoice);
    await page.getByRole("button", { name: "Upload PDF", exact: true }).click();
    await expect(library.getByRole("button", { name: "comparison-invoice.pdf", exact: true })).toBeVisible();
    expect((await request.get(response.url())).status()).toBe(410);
  } else {
    await expect(library.getByRole("status").filter({ hasText: "Deletion · comparison-invoice.pdf" })).toContainText("succeeded");
  }
});
