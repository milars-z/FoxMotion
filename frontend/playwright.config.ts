import { defineConfig } from "@playwright/test";
import path from "node:path";
const python = path.resolve(
  "..",
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 60000,
  use: {
    baseURL: "http://127.0.0.1:17860",
    channel: process.platform === "win32" ? "msedge" : "chromium",
    viewport: { width: 1440, height: 960 },
  },
  webServer: [{
    command: '"' + python + '" ../scripts/browser_test_server.py',
    url: "http://127.0.0.1:17860/api/health",
    reuseExistingServer: false,
    timeout: 30000,
  }, {
    command: "node node_modules/vite/bin/vite.js --port 15173",
    env: { STUDIO_PORT: "17860" },
    url: "http://127.0.0.1:15173/",
    reuseExistingServer: false,
    timeout: 30000,
  }],
});
