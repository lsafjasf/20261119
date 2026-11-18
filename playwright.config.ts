import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const configuredPath =
  process.env.PLAYWRIGHT_CHROMIUM_PATH ??
  "/home/hj/.cache/ms-playwright/chromium-1255/chrome-linux64/chrome";
const executablePath = existsSync(configuredPath) ? configuredPath : undefined;

export default defineConfig({
  testDir: "./test/e2e",
  timeout: 45_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:4287",
    trace: "retain-on-failure",
    launchOptions: {
      ...(executablePath ? { executablePath } : {}),
      args: [
        "--no-sandbox",
        "--autoplay-policy=no-user-gesture-required",
        "--use-fake-ui-for-media-stream",
      ],
    },
  },
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:4287",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
