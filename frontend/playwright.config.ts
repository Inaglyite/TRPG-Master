import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const localChromium = "/snap/bin/chromium";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 12_000 },
  reporter: [["list"]],
  use: {
    browserName: "chromium",
    launchOptions: existsSync(localChromium)
      ? { executablePath: localChromium }
      : {},
    headless: true,
    ignoreHTTPSErrors: true,
    viewport: { width: 1440, height: 900 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
