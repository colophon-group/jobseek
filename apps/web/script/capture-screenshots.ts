/**
 * Capture marketing screenshots for each locale × theme combination.
 *
 * Usage:
 *   npx tsx script/capture-screenshots.ts [--base-url http://localhost:3000]
 *
 * Prerequisites:
 *   - A running Next.js dev/preview server (defaults to http://localhost:3000)
 *   - Playwright browsers installed:  npx playwright install chromium
 *   - Database seeded with data so pages have content to show
 *
 * Output:
 *   public/screenshots/{locale}/feature{N}-{theme}.png
 *   (4 locales × 2 themes × 2 features = 16 images)
 */

import { chromium, type Page } from "playwright";
import { mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const LOCALES = ["en", "de", "fr", "it"] as const;
const THEMES = ["light", "dark"] as const;

const BASE_URL = process.argv.find((a) => a.startsWith("--base-url="))
  ?.split("=")[1] ?? process.argv[process.argv.indexOf("--base-url") + 1] ?? "http://localhost:3000";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(__dirname, "..", "public", "screenshots");

const WIDTH = 1200;
const HEIGHT = 630;

/**
 * Screenshot definitions — each maps to one feature section on the landing page.
 *
 * `path` is relative to `/{locale}`. The script navigates there, waits for
 * hydration, and takes a viewport-sized screenshot at 2× device scale.
 */
const FEATURES: { path: string; name: string }[] = [
  { path: "/explore?q=software+engineer&loc=Switzerland", name: "feature1" },
  { path: "/company/amazon", name: "feature2" },
];

async function setTheme(page: Page, theme: "light" | "dark") {
  // next-themes stores the theme in localStorage and applies .dark / .light class
  await page.evaluate((t) => {
    localStorage.setItem("theme", t);
    document.documentElement.classList.remove("light", "dark");
    document.documentElement.classList.add(t);
    // Trigger next-themes storage event handler
    window.dispatchEvent(new StorageEvent("storage", { key: "theme", newValue: t }));
  }, theme);
  // Wait for any CSS transitions
  await page.waitForTimeout(300);
}

async function waitForHydration(page: Page) {
  // Wait for Next.js to finish hydration — the __NEXT_DATA__ script is present
  // after SSR, and React hydration completes when interactive elements respond.
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(500);
}

async function run() {
  console.log(`Capturing screenshots from ${BASE_URL}`);
  console.log(`Output directory: ${OUT_DIR}\n`);

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: WIDTH, height: HEIGHT },
    deviceScaleFactor: 2,
    // Prevent cookie banners / auth redirects
    storageState: undefined,
  });

  let captured = 0;

  for (const locale of LOCALES) {
    const localeDir = join(OUT_DIR, locale);
    mkdirSync(localeDir, { recursive: true });

    for (const feature of FEATURES) {
      const url = `${BASE_URL}/${locale}${feature.path}`;

      for (const theme of THEMES) {
        const page = await context.newPage();

        // Pre-set theme and dismiss cookie consent before navigation
        await page.addInitScript((t) => {
          localStorage.setItem("theme", t);
          localStorage.setItem("cookie-consent", "1");
        }, theme);

        console.log(`  ${locale}/${feature.name}-${theme} → ${url}`);

        try {
          await page.goto(url, { waitUntil: "networkidle", timeout: 30_000 });
          await setTheme(page, theme);
          await waitForHydration(page);

          const outPath = join(localeDir, `${feature.name}-${theme}.png`);
          await page.screenshot({ path: outPath, type: "png" });
          captured++;
        } catch (err) {
          console.error(`    FAILED: ${err instanceof Error ? err.message : err}`);
        } finally {
          await page.close();
        }
      }
    }
  }

  await browser.close();
  console.log(`\nDone — ${captured} screenshots captured.`);
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
