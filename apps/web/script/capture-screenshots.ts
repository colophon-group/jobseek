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
 *   - LOGIN_UI / PWD_UI set in .env.local (for authenticated pages)
 *
 * Output:
 *   public/screenshots/{locale}/feature{N}-{theme}.png
 *   (4 locales × 2 themes × 2 features = 16 images)
 */

import { chromium, type BrowserContext, type Page } from "playwright";
import { mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { config } from "dotenv";

const __dirname = dirname(fileURLToPath(import.meta.url));
config({ path: join(__dirname, "..", ".env.local") });

const LOCALES = ["en", "de", "fr", "it"] as const;
const THEMES = ["light", "dark"] as const;

const BASE_URL = process.argv.find((a) => a.startsWith("--base-url="))
  ?.split("=")[1] ?? process.argv[process.argv.indexOf("--base-url") + 1] ?? "http://localhost:3000";

const OUT_DIR = join(__dirname, "..", "public", "screenshots");

const WIDTH = 1200;
const HEIGHT = 630;

const LOGIN_EMAIL = process.env.LOGIN_UI;
const LOGIN_PWD = process.env.PWD_UI;

/**
 * Screenshot definitions — each maps to one feature section on the landing page.
 *
 * `path` is relative to `/{locale}`. The script navigates there, waits for
 * hydration, and takes a viewport-sized screenshot at 2× device scale.
 */
const FEATURES: { path: string; name: string; requiresAuth: boolean }[] = [
  { path: "/explore?q=software+engineer&loc=Switzerland", name: "feature1", requiresAuth: false },
  { path: "/my-jobs?show=dd0e09ec-e452-416a-85f6-593cec9c9923", name: "feature2", requiresAuth: true },
];

async function login(context: BrowserContext) {
  if (!LOGIN_EMAIL || !LOGIN_PWD) {
    console.warn("  LOGIN_UI / PWD_UI not set — skipping auth pages");
    return false;
  }

  console.log("  Logging in via API...");

  // Sign in via Better Auth API to get a session cookie
  const res = await fetch(`${BASE_URL}/api/auth/sign-in/email`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Origin": process.env.BETTER_AUTH_URL ?? "http://localhost:3000" },
    body: JSON.stringify({ email: LOGIN_EMAIL, password: LOGIN_PWD }),
  });

  if (!res.ok) {
    const body = await res.text();
    console.warn(`  Login failed (${res.status}): ${body}`);
    return false;
  }

  // Extract session cookie from response and inject into browser context
  const setCookie = res.headers.getSetCookie();
  for (const header of setCookie) {
    const [nameVal] = header.split(";");
    const eqIdx = nameVal.indexOf("=");
    const name = nameVal.slice(0, eqIdx);
    const value = nameVal.slice(eqIdx + 1);
    await context.addCookies([{
      name,
      value,
      domain: new URL(BASE_URL).hostname,
      path: "/",
    }]);
  }

  console.log("  Logged in successfully\n");
  return true;
}

async function setTheme(page: Page, theme: "light" | "dark") {
  await page.evaluate((t) => {
    localStorage.setItem("theme", t);
    document.documentElement.classList.remove("light", "dark");
    document.documentElement.classList.add(t);
    window.dispatchEvent(new StorageEvent("storage", { key: "theme", newValue: t }));
  }, theme);
  await page.waitForTimeout(300);
}

async function waitForHydration(page: Page) {
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
  });

  // Dismiss cookie banner globally for the context
  await context.addInitScript(() => {
    localStorage.setItem("cookie-consent", "1");
  });

  // Log in once — session cookies persist across all pages in this context
  const loggedIn = await login(context);

  let captured = 0;

  for (const locale of LOCALES) {
    const localeDir = join(OUT_DIR, locale);
    mkdirSync(localeDir, { recursive: true });

    for (const feature of FEATURES) {
      if (feature.requiresAuth && !loggedIn) {
        console.log(`  SKIP ${locale}/${feature.name} (not logged in)`);
        continue;
      }

      const url = `${BASE_URL}/${locale}${feature.path}`;

      for (const theme of THEMES) {
        const page = await context.newPage();

        // Pre-set theme before navigation
        await page.addInitScript((t) => {
          localStorage.setItem("theme", t);
          localStorage.setItem("cookie-consent", "1");
        }, theme);

        console.log(`  ${locale}/${feature.name}-${theme} → ${url}`);

        try {
          await page.goto(url, { waitUntil: "networkidle", timeout: 30_000 });
          await setTheme(page, theme);
          await waitForHydration(page);

          // Click Ok on cookie banner if it still appears
          const okBtn = page.locator('button:has-text("Ok")').first();
          if (await okBtn.isVisible({ timeout: 300 }).catch(() => false)) {
            await okBtn.click();
            await page.waitForTimeout(300);
          }

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
