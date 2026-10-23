import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const project = path.resolve(import.meta.dirname, "..");
const output = path.join(project, "demo", "fallback_frames");
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const url = process.argv[2] || "http://127.0.0.1:8000";

fs.mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: chrome });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
await page.emulateMedia({ reducedMotion: "reduce" });
await page.goto(url, { waitUntil: "networkidle" });
await page.getByRole("button", { name: /Launch presentation mode/ }).click();
await page.locator("#deck-modal.open").waitFor();
await page.waitForTimeout(100);

for (let slide = 1; slide <= 10; slide += 1) {
  await page.locator("#deck-stage").screenshot({ path: path.join(output, `slide-${String(slide).padStart(2, "0")}.png`) });
  if (slide < 10) {
    await page.getByRole("button", { name: "Next slide" }).click();
    await page.waitForTimeout(50);
  }
}

await browser.close();
console.log(`Captured 10 presentation frames in ${output}`);
