// docs/ screenshots from the REAL stack (docker compose up, dashboard on :8080). Run: SHOTS=1 e2e/run.sh
// Drives the existing demo commands (replay speed) until the model raises a real alarm, then captures
// the Operator view (dark, light, phone), and the Engineer view.
import { expect, test, type Page } from "@playwright/test";
import mqtt from "mqtt";

const DOCS = "../docs";
const desktop = { width: 1366, height: 900 };
const phone = { width: 390, height: 844 };

// Full page, but taken by growing the viewport first: Playwright's own fullPage resize makes the charts
// redraw during the capture, which can leave them blank in the image.
async function shot(page: Page, name: string) {
  const size = page.viewportSize()!;
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  await page.setViewportSize({ width: size.width, height });
  await page.waitForTimeout(1500); // charts redraw + one screen tick
  await page.screenshot({ path: `${DOCS}/${name}.png` });
  await page.setViewportSize(size);
}

test("docs screenshots", async ({ page }) => {
  const client = await mqtt.connectAsync("ws://localhost:9001", { clientId: "edgeguard-shots" });
  const cmd = (c: object) => client.publishAsync("edgeguard/machine1/cmd", JSON.stringify(c), { qos: 1 });
  try {
    await page.setViewportSize(desktop);
    await page.addInitScript(() => localStorage.setItem("edgeguard.view", "operator"));
    await page.goto("/?theme=dark");
    await cmd({ cmd: "drift", on: false });
    await cmd({ cmd: "speed", cycle_ms: 100 }); // 10x until the model raises a real alarm
    await expect(page.locator('[data-box="hero"]')).toContainText("Act now", { timeout: 90_000 });
    await cmd({ cmd: "speed", cycle_ms: 1000 });
    await page.waitForTimeout(8000); // a few readings at 1x after the alarm
    await shot(page, "dashboard_operator");

    await page.locator('[data-box="theme"]').click();
    await shot(page, "dashboard_operator_light");
    await page.locator('[data-box="theme"]').click();

    await page.locator('[data-box="view-engineer"]').click();
    await shot(page, "dashboard_engineer");
    await page.setViewportSize(phone);
    await shot(page, "dashboard_engineer_phone");
    await page.setViewportSize(desktop);
    await page.locator('[data-box="view-operator"]').click();

    await page.setViewportSize(phone);
    await shot(page, "dashboard_operator_phone_dark");
    await page.locator('[data-box="theme"]').click();
    await shot(page, "dashboard_operator_phone_light");
    await page.locator('[data-box="theme"]').click();
  } finally {
    await cmd({ cmd: "speed", cycle_ms: 1000 });
    await client.endAsync();
  }
});
