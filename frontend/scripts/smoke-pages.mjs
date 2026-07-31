/**
 * Section 10 frontend smoke: load key pages on :3001, capture console errors
 * and failed network responses, report pass/fail per page.
 *
 * Usage: npm run smoke:pages
 * Env:   FRONTEND_BASE_URL (default http://127.0.0.1:3001)
 */
import { chromium } from "playwright";

const BASE = (process.env.FRONTEND_BASE_URL || "http://localhost:3001").replace(/\/$/, "");
const PAGES = [
  "/workforce",
  "/evidence",
  "/findings",
  "/obligations",
  "/documents",
  "/auditor",
];

/** Ignore noisy/benign failures that are not app regressions. */
function isIgnorableFailedRequest(url, status) {
  if (status === 404 && (url.includes("favicon") || url.endsWith(".map"))) return true;
  return false;
}

function isIgnorableConsole(text) {
  const t = text.toLowerCase();
  // Next.js / React DevTools noise occasionally appears in headless
  if (t.includes("download the react devtools")) return true;
  return false;
}

async function checkPage(browser, path) {
  const page = await browser.newPage();
  const consoleErrors = [];
  const failedNet = [];

  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    if (!isIgnorableConsole(text)) consoleErrors.push(text);
  });
  page.on("pageerror", (err) => {
    consoleErrors.push(String(err));
  });
  page.on("response", (res) => {
    const status = res.status();
    if (status < 400) return;
    const url = res.url();
    if (isIgnorableFailedRequest(url, status)) return;
    failedNet.push(`${status} ${url}`);
  });

  let loadError = null;
  try {
    await page.goto(`${BASE}${path}`, { waitUntil: "networkidle", timeout: 60_000 });
    await page.waitForTimeout(1500);
  } catch (err) {
    loadError = String(err);
  }

  await page.close();
  const pass = !loadError && consoleErrors.length === 0 && failedNet.length === 0;
  return { path, pass, loadError, consoleErrors, failedNet };
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const results = [];
  try {
    for (const path of PAGES) {
      const result = await checkPage(browser, path);
      results.push(result);
      const tag = result.pass ? "PASS" : "FAIL";
      console.log(`\n[${tag}] ${path}`);
      if (result.loadError) console.log(`  load: ${result.loadError}`);
      if (result.consoleErrors.length) {
        console.log("  console errors:");
        for (const e of result.consoleErrors.slice(0, 10)) console.log(`    - ${e}`);
      }
      if (result.failedNet.length) {
        console.log("  failed network:");
        for (const e of result.failedNet.slice(0, 15)) console.log(`    - ${e}`);
      }
    }
  } finally {
    await browser.close();
  }

  const failed = results.filter((r) => !r.pass);
  console.log("\n=== SECTION 10 PLAYWRIGHT SMOKE ===");
  for (const r of results) console.log(`${r.path}: ${r.pass ? "PASS" : "FAIL"}`);
  console.log(failed.length === 0 ? "OVERALL PASS" : `OVERALL FAIL (${failed.length}/${results.length})`);
  process.exit(failed.length === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
