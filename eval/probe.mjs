// Shared helper for Evaluator throwaway scripts (see eval/tmp/*.mjs). Keeps each criterion
// check to ~10 lines: launch headless Chromium, hand back a page, clean up automatically.
//
// Usage (a complete criterion check, ~10 lines):
//
//   import { probe, shot } from "../probe.mjs";
//
//   await probe(async (page) => {
//     await page.goto("/expenses");
//     await page.click("text=Export CSV");
//     const download = await page.waitForEvent("download");
//     const path = await download.path();
//     await shot(page, "state/screenshots/F007/attempt1/01-export-csv.png");
//     const header = (await import("node:fs")).readFileSync(path, "utf8").split("\n")[0];
//     if (header.trim() !== "Date,Category,Amount") {
//       throw new Error(`expected header "Date,Category,Amount", got "${header.trim()}"`);
//     }
//   });

import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { dirname } from "node:path";

/**
 * Launch headless Chromium, open a page, run `fn(page)`, and always clean up.
 * baseURL defaults to the APP_URL environment variable (set by the orchestrator/evaluator
 * session). Exits the process with a nonzero code and prints the error if `fn` throws.
 */
export async function probe(fn, { baseURL = process.env.APP_URL } = {}) {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ baseURL });
    const page = await context.newPage();
    try {
      await fn(page);
    } finally {
      await context.close();
    }
  } catch (err) {
    console.error("probe failed:", err && err.stack ? err.stack : err);
    process.exitCode = 1;
    throw err;
  } finally {
    await browser.close();
  }
}

/** Screenshot `page` to `path`, creating any missing parent directories first. */
export async function shot(page, path) {
  await mkdir(dirname(path), { recursive: true });
  await page.screenshot({ path, fullPage: true });
  return path;
}
