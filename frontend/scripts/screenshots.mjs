// Regenerates docs/screenshots/*.png against a running API.
// Usage: IL_URL=http://127.0.0.1:8000 npm run screenshots
import { chromium } from "playwright";

const OUT = new URL("../../docs/screenshots", import.meta.url).pathname;
const B = process.env.IL_URL || "http://127.0.0.1:8000";  // make serve
const browser = await chromium.launch();

async function shoot(name, path, act, { w = 1440, h = 900 } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  await page.addInitScript(() => localStorage.setItem("recto-theme", "light"));
  await page.goto(B + path, { waitUntil: "networkidle" });
  await page.waitForTimeout(1800);
  if (act) await act(page);
  await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log("shot:", name);
  await ctx.close();
}

await shoot("00-landing", "/", (p) => p.waitForTimeout(1600));          // the loop draws on
await shoot("01-dashboard", "/app");
await shoot("02-answer-result", "/app", async (p) => {
  await p.fill("#question", "How much net income did JPMorgan report?");
  await p.click(".ask-bar .btn");
  await p.waitForSelector(".answer", { timeout: 120000 });
  await p.waitForTimeout(2600);
}, { h: 1000 });
await shoot("03-company-browser", "/app", async (p) => {
  await p.fill(".picker input", "Coca");
  await p.waitForSelector(".picker-menu .row-btn", { timeout: 20000 });
  await p.click(".picker-menu .row-btn");
  await p.waitForSelector(".card .row-btn", { timeout: 30000 }).catch(() => {});
  await p.waitForTimeout(900);
});
await shoot("04-command-palette", "/app", async (p) => {
  await p.keyboard.press("Meta+k");
  await p.waitForTimeout(400);
  await p.fill(".palette-head input", "net");
  await p.waitForTimeout(900);
});
await browser.close();
