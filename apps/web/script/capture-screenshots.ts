/**
 * Capture marketing screenshots for each locale × theme combination.
 *
 * Usage:
 *   pnpm screenshots --base-url https://jseek.co --features feature3,narrowed
 *   Optional: --storage-state /private/path/session.json (never commit this file)
 *
 * Prerequisites:
 *   - A running Next.js **production** server (defaults to http://localhost:3000)
 *     Run: pnpm build && pnpm start
 *     (Dev mode adds overlays that pollute screenshots)
 *   - Playwright browsers installed:  npx playwright install chromium
 *   - Database seeded with data so pages have content to show
 *   - LOGIN_UI / PWD_UI set in .env.local (for authenticated pages)
 *
 * Output:
 *   public/screenshots/{locale}/feature{N}-{theme}.png
 *   feature3 uses the versioned feature3-2026-09 filename.
 *   .github/assets/readme/narrowed.png (English, light theme)
 */

import { chromium, type BrowserContext, type Page } from "playwright";
import { mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import type { AiFilterUiState } from "../src/lib/ai-filter/ui-contract";
import { config } from "dotenv";
import { logExternalError } from "../src/lib/safe-external-error";

const __dirname = dirname(fileURLToPath(import.meta.url));
config({ path: join(__dirname, "..", ".env.local") });

const LOCALES = ["en", "de", "fr", "it"] as const;
const THEMES = ["light", "dark"] as const;

const { values } = parseArgs({
  options: {
    "base-url": { type: "string", default: "http://localhost:3000" },
    features: { type: "string", default: "feature1,feature2,feature3,narrowed" },
    "storage-state": { type: "string" },
  },
});
const BASE_URL = values["base-url"].replace(/\/$/, "");
const selectedFeatures = new Set(values.features.split(","));

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
const FEATURES: { path: string; name: string; requiresAuth: boolean; height?: number }[] = [
  { path: "/explore?q=software+engineer&loc=Switzerland", name: "feature1", requiresAuth: false },
  { path: "/my-jobs", name: "feature2", requiresAuth: true },
  { path: "/watchlists/5fe6eeb5-658d-4498-b942-9d1208d0f521", name: "feature3", requiresAuth: true },
  { path: "/watchlists/c47beab8-3e96-4032-af4b-d9843bdba631", name: "narrowed", requiresAuth: true, height: 1100 },
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
    console.warn(`  Login failed (${res.status})`);
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
      secure: new URL(BASE_URL).protocol === "https:",
    }]);
  }

  console.log("  Logged in successfully\n");
  return true;
}

async function setTheme(page: Page, theme: "light" | "dark") {
  await page.evaluate((t) => {
    localStorage.setItem("theme", t);
    window.dispatchEvent(new StorageEvent("storage", { key: "theme", newValue: t }));
  }, theme);
  await page.waitForFunction((t) => document.documentElement.classList.contains(t), theme);
}

async function waitForHydration(page: Page) {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(500);
}

async function run() {
  console.log(`Capturing screenshots from ${BASE_URL}`);
  console.log(`Output directory: ${OUT_DIR}\n`);

  for (const name of selectedFeatures) {
    if (!FEATURES.some((feature) => feature.name === name)) throw new Error(`Unknown feature: ${name}`);
  }
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: WIDTH, height: HEIGHT },
    deviceScaleFactor: 2,
    storageState: values["storage-state"],
  });

  // Shared Narrowed screenshots use the guest view so opening results cannot
  // start an owner evaluation or change the saved feed.
  const guestContext = await browser.newContext({
    viewport: { width: WIDTH, height: HEIGHT },
    deviceScaleFactor: 2,
  });

  // Dismiss cookie banner globally
  await context.addInitScript(() => {
    localStorage.setItem("cookie-consent", "1");
  });

  // Log in once — session cookies persist across all pages in this context
  const loggedIn = values["storage-state"] ? true : await login(context);
  if (!loggedIn && FEATURES.some((feature) => selectedFeatures.has(feature.name) && feature.requiresAuth)) {
    await browser.close();
    throw new Error("Authenticated screenshots require a valid account session");
  }

  let captured = 0;
  let failed = 0;

  for (const locale of LOCALES) {
    const localeDir = join(OUT_DIR, locale);
    mkdirSync(localeDir, { recursive: true });

    for (const feature of FEATURES) {
      if (!selectedFeatures.has(feature.name)) continue;
      if (feature.name === "narrowed" && locale !== "en") continue;
      if (feature.requiresAuth && !loggedIn) {
        console.log(`  SKIP ${locale}/${feature.name} (not logged in)`);
        continue;
      }

      const url = `${BASE_URL}/${locale}${feature.path}`;

      for (const theme of THEMES) {
        if (feature.name === "narrowed" && theme !== "light") continue;
        const page = await (feature.name === "narrowed" ? guestContext : context).newPage();
        await page.setViewportSize({ width: WIDTH, height: feature.height ?? HEIGHT });

        // Pre-set theme and locale before navigation to prevent
        // PreferencesInitializer from redirecting to the user's saved locale
        await page.addInitScript(({ t, loc }) => {
          localStorage.setItem("theme", t);
          localStorage.setItem("cookie-consent", "1");
          localStorage.setItem("pref-locale", loc);
          localStorage.setItem("pref-locale-updated-at", new Date().toISOString());
        }, { t: theme, loc: locale });

        console.log(`  ${locale}/${feature.name}-${theme} → ${url}`);

        try {
          await page.goto(url, { waitUntil: "networkidle", timeout: 60_000 });
          await setTheme(page, theme);
          await waitForHydration(page);

          // Wait for locale redirect to settle (PreferencesInitializer)
          await page.waitForTimeout(500);
          await page.waitForLoadState("networkidle");

          // Click Ok on cookie banner if it still appears
          const okBtn = page.locator('button:has-text("Ok")').first();
          if (await okBtn.isVisible({ timeout: 300 }).catch(() => false)) {
            await okBtn.click();
            await page.waitForTimeout(300);
          }

          // Expand advanced filters panel on the search page (feature1)
          if (feature.name === "feature1") {
            const filtersBtn = page.locator('button:has-text("Filters")').first();
            if (await filtersBtn.isVisible({ timeout: 2_000 }).catch(() => false)) {
              await filtersBtn.click();
              await page.waitForTimeout(400);
            }
          }

          // Dismiss tip banner on watchlist page (feature3)
          const gotItBtn = page.locator('button:has-text("Got it")').first();
          if (await gotItBtn.isVisible({ timeout: 500 }).catch(() => false)) {
            await gotItBtn.click();
            await page.waitForTimeout(300);
          }

          // Click first job to open detail panel (for my-jobs page)
          if (feature.name === "feature2") {
            const jobRow = page.locator('[role="button"][tabindex="0"]').first();
            if (await jobRow.isVisible({ timeout: 3_000 }).catch(() => false)) {
              await jobRow.click();
              await page.waitForSelector('text="View posting"', { timeout: 5_000 }).catch(() => {});
              await page.waitForLoadState("networkidle");
              await page.waitForTimeout(500);
            }
          }

          // Verify the real rendered product; never alter its content for a capture.
          if (new URL(page.url()).pathname !== new URL(url).pathname) {
            throw new Error("Screenshot navigation did not reach the requested locale and page");
          }
          if (feature.path.startsWith("/watchlists/")) {
            const session = await context.request.get(`${BASE_URL}/api/auth/get-session`);
            const identity = await session.json();
            if (!session.ok() || !identity?.user?.id) throw new Error("Screenshot session expired");
            await page.locator('main:visible a[href*="/company/"]').first().waitFor();
            await page.locator('button:visible:has(svg.lucide-share2), button:visible:has(svg.lucide-share-2)').first().waitFor();
          }
          if (feature.name === "narrowed") {
            const stateResponse = await context.request.get(`${BASE_URL}/api/web${feature.path}/ai-filter`);
            if (!stateResponse.ok()) throw new Error("Could not verify Narrowed state");
            const state: AiFilterUiState = await stateResponse.json();
            if (!state.enabled || state.status !== "caught_up" || !state.query || state.counts.accepted < 1) {
              throw new Error("Narrowed must have completed results before capture");
            }
            const toggle = page.locator('main:visible aside button[aria-expanded="false"]');
            const panelId = await toggle.getAttribute("aria-controls");
            await toggle.click();
            if (!panelId) throw new Error("Narrowed toggle has no associated results panel");
            const panel = page.locator(`[id=${JSON.stringify(panelId)}]`);
            await panel.waitFor({ state: "visible" });
            await panel.getByText(state.query, { exact: true }).waitFor();
            await page.locator("main:visible").getByRole("button", { name: / — / }).first().waitFor();
          }
          await page.evaluate(() => document.fonts.ready);
          await page.waitForFunction(() => Array.from(document.images).every((image) => !image.checkVisibility() || image.complete));
          await page.waitForTimeout(300); // Let theme/panel transitions finish.

          const assetName = feature.name === "feature3" ? "feature3-2026-09" : feature.name;
          const outPath = feature.name === "narrowed"
            ? join(__dirname, "../../../.github/assets/readme/narrowed.png")
            : join(localeDir, `${assetName}-${theme}.png`);
          await page.screenshot({ path: outPath, type: "png" });
          captured++;
        } catch (err) {
          failed++;
          logExternalError("error", { service: "external_http", operation: "capture_screenshot" }, err);
        } finally {
          await page.close();
        }
      }
    }
  }

  await browser.close();
  console.log(`\nDone — ${captured} screenshots captured; ${failed} failed.`);
  if (failed) process.exitCode = 1;
}

run().catch((err) => {
  logExternalError("error", { service: "external_http", operation: "capture_screenshots" }, err);
  process.exit(1);
});
