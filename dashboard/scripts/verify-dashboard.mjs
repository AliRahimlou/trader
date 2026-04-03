import { chromium } from "playwright";

const baseUrl = process.env.DASHBOARD_BASE_URL || "http://127.0.0.1:5173";

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1400 } });
  page.on("console", (message) => {
    console.log(`BROWSER:${message.type()}:${message.text()}`);
  });
  page.on("pageerror", (error) => {
    console.log(`PAGEERROR:${error.message}`);
  });
  page.on("dialog", async (dialog) => {
    console.log(`DIALOG:${dialog.message()}`);
    await dialog.accept();
  });

  await page.goto(baseUrl, { waitUntil: "load" });
  await page.waitForSelector("text=Your paper portfolio");
  await page.waitForTimeout(1000);
  console.log("HOME_LOADED");

  const toggle = page.locator("button.operator-toggle");
  console.log(`TOGGLE_BEFORE:${(await toggle.textContent())?.trim()}`);
  await toggle.scrollIntoViewIfNeeded();
  await toggle.click();
  await page.waitForTimeout(200);
  console.log(`TOGGLE_AFTER:${(await toggle.textContent())?.trim()}`);

  await page.getByRole("link", { name: "Bot", exact: true }).click();
  await page.waitForURL("**/bot");
  console.log("BOT_LOADED");

  const startButton = page.getByRole("button", { name: "Start bot" });
  const stopButton = page.getByRole("button", { name: "Stop bot" });
  const startDisabled = await startButton.isDisabled();
  const stopDisabled = await stopButton.isDisabled();
  console.log(`START_DISABLED:${startDisabled}`);
  console.log(`STOP_DISABLED:${stopDisabled}`);

  if (startDisabled && !stopDisabled) {
    await stopButton.click();
    await page.waitForTimeout(2500);
    console.log(`RUNNER_AFTER_INITIAL_STOP:${(await page.locator(".topbar-right").textContent())?.replace(/\\s+/g, " ").trim()}`);
  }

  if (!(await startButton.isDisabled())) {
    await startButton.click();
    await page.waitForTimeout(2000);
    console.log(`RUNNER_AFTER_START:${(await page.locator(".topbar-right").textContent())?.replace(/\\s+/g, " ").trim()}`);
  }

  if (!(await stopButton.isDisabled())) {
    await stopButton.click();
    await page.waitForTimeout(2500);
    console.log(`RUNNER_AFTER_STOP:${(await page.locator(".topbar-right").textContent())?.replace(/\\s+/g, " ").trim()}`);
  }

  await page.screenshot({ path: "../runtime/dashboard-validation.png", fullPage: true });
  console.log("SCREENSHOT:runtime/dashboard-validation.png");
  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
