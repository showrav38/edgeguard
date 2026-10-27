// Headless-browser tests (Playwright), run in the Playwright Docker image: `npm run test:e2e` (= e2e/run.sh)
//   layout.e2e.ts: nothing moves. A private MQTT broker (port 19001, started by e2e/run.sh) feeds synthetic states to a
//                  dev server built for that broker; every card and button must keep its box.
//   shots.e2e.ts:  docs/ screenshots from the real stack on :8080 (only with SHOTS=1).
import { defineConfig } from "@playwright/test";

const shots = process.env.SHOTS === "1";

export default defineConfig({
  testDir: "e2e",
  testMatch: process.env.E2E_MATCH ?? (shots ? "shots.e2e.ts" : "layout.e2e.ts"),
  timeout: 240_000,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: shots ? "http://localhost:8080" : "http://localhost:5179" },
  webServer: shots ? undefined : {
    command: "npx vite --port 5179 --strictPort",
    port: 5179,
    reuseExistingServer: false,
    env: { VITE_MQTT_URL: "ws://localhost:19001" },
  },
});
