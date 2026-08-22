import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  timeout: 45_000,
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command:
        "../backend/.venv/bin/uvicorn tests.e2e_server:app --app-dir ../backend --host 127.0.0.1 --port 8000",
      port: 8000,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 4173 --strictPort",
      port: 4173,
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
