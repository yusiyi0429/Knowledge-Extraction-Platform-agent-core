import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));
const dataDir = mkdtempSync(join(tmpdir(), "openjiuwen-workbench-e2e-"));

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  outputDir: "./output/playwright/test-results",
  reporter: [["list"], ["html", { outputFolder: "./output/playwright/report", open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:8765",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    command: `cd "${repoRoot}" && WORKBENCH_DATA_DIR="${dataDir}" uv run python -m examples.knowledge_extraction_workbench.backend`,
    url: "http://127.0.0.1:8765/api/v1/health",
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
  projects: [
    { name: "chromium-1440", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } },
    { name: "chromium-1280", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } },
  ],
});
