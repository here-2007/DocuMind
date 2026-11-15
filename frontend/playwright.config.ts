import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 120_000,
  workers: 1,
  use: { baseURL: process.env.DOCUMIND_TEST_WEB_URL, browserName: "chromium", channel: "chromium" },
});
