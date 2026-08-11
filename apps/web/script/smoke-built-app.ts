import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { chromium, type Browser } from "playwright";
import { logExternalError } from "../src/lib/safe-external-error";

const port = Number(process.env.SMOKE_PORT ?? "3100");
const baseUrl = `http://127.0.0.1:${port}`;
const routes = ["/en", "/en/explore", "/en/companies/request"];
const localizedExploreRoutes = [
  ["/en/explore", "Explore Jobs"],
  ["/de/explore", "Jobs entdecken"],
  ["/fr/explore", "Explorer les emplois"],
  ["/it/explore", "Esplora lavori"],
  // Filter-bearing documents are rewritten to the same cached shell. The
  // browser hydrates the real URL and fetches filtered data afterwards, but
  // raw HTML must remain meaningful rather than collapsing to loading copy.
  ["/de/explore?q=python", "Jobs entdecken"],
] as const;
const navigationActionRoutes = [
  ["/en/explore", 0],
  ["/de/explore?q=python", 1],
  ["/en/company/stripe", 0],
  ["/en/colophongroup/swe-zurich", 0],
] as const;
const discoveryNotFoundRoutes = [
  "/api",
  "/api-docs",
  "/api-reference",
  "/developer",
  "/developers",
  "/mcp.json",
  "/openapi.yaml",
] as const;
const discoveryRedirectRoutes = [
  ["/llms.txt", "/.well-known/llms.txt"],
  ["/openapi.json", "/api/openapi.json"],
] as const;
const missingResourceRoutes = [
  ["/en/company/definitely-not-a-real-company", "Company not found"],
  ["/de/company/definitely-not-a-real-company", "Unternehmen nicht gefunden"],
  ["/en/not-a-real-user/not-a-real-watchlist", "Watchlist not found"],
  ["/fr/not-a-real-user/not-a-real-watchlist", "Liste de surveillance introuvable"],
  ["/en/alice/private.list", "Watchlist not found"],
] as const;

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForServer(timeoutMs = 45_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(baseUrl, { redirect: "manual" });
      if (response.status < 500) return;
    } catch {
      // Server not listening yet.
    }
    await delay(500);
  }
  throw new Error(`Timed out waiting for ${baseUrl}`);
}

function startServer() {
  // Standalone deployments receive these values from the container runtime.
  // Load the local Next override for smoke so dynamic 200 fixtures use
  // their real read paths without printing or copying any secret values.
  const localEnv = path.join(process.cwd(), ".env.local");
  if (fs.existsSync(localEnv)) {
    process.loadEnvFile(localEnv);
  }
  const standaloneServer = path.join(
    process.cwd(),
    ".next",
    "standalone",
    "apps",
    "web",
    "server.js",
  );
  const pnpmCli = process.env.npm_execpath;
  const useStandalone = fs.existsSync(standaloneServer);
  if (useStandalone) {
    // Next's standalone output intentionally excludes public/static assets.
    // Mirror apps/web/Dockerfile so browser smoke loads and hydrates the same
    // client chunks as the production image instead of exercising a silent
    // JavaScript-disabled page whose /_next/static requests all return 404.
    const standaloneApp = path.dirname(standaloneServer);
    const assetCopies = [
      [path.join(process.cwd(), ".next", "static"), path.join(standaloneApp, ".next", "static")],
      [path.join(process.cwd(), "public"), path.join(standaloneApp, "public")],
    ] as const;
    for (const [source, destination] of assetCopies) {
      if (fs.existsSync(source)) {
        fs.cpSync(source, destination, { recursive: true, force: true });
      }
    }
  }
  const command = useStandalone ? process.execPath : pnpmCli ? process.execPath : "pnpm";
  const args = useStandalone
    ? [standaloneServer]
    : pnpmCli
      ? [pnpmCli, "exec", "next", "start", "-p", String(port)]
      : ["exec", "next", "start", "-p", String(port)];
  const child = spawn(command, args, {
    cwd: process.cwd(),
    env: { ...process.env, PORT: String(port) },
    stdio: ["ignore", "pipe", "pipe"],
  });

  child.stdout.on("data", (data) => process.stdout.write(data));
  child.stderr.on("data", (data) => process.stderr.write(data));
  return child;
}

async function smoke(browser: Browser, route: string) {
  const page = await browser.newPage();
  const response = await page.goto(`${baseUrl}${route}`, { waitUntil: "domcontentloaded" });
  if (!response || response.status() >= 400) {
    throw new Error(`${route} returned HTTP ${response?.status() ?? "no response"}`);
  }
  await page.locator("body").waitFor({ state: "attached" });
  const bodyText = ((await page.locator("body").textContent()) ?? "").trim();
  if (bodyText.length < 20) {
    throw new Error(`${route} rendered suspiciously little text`);
  }
  console.log(`smoke ok ${route}`);
  await page.close();
}

function withoutScripts(html: string) {
  return html
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/giu, "")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/giu, "");
}

async function smokeExploreRawHtml(route: string, heading: string) {
  const response = await fetch(`${baseUrl}${route}`, {
    headers: { accept: "text/html" },
  });
  if (response.status !== 200) {
    throw new Error(`${route} raw HTML returned HTTP ${response.status}`);
  }
  const visibleHtml = withoutScripts(await response.text());
  if (
    !visibleHtml.includes(heading) ||
    !visibleHtml.includes("data-explore-static-results") ||
    !visibleHtml.includes("data-search-result-company=")
  ) {
    throw new Error(
      `${route} raw HTML did not contain its localized heading and meaningful initial company results`,
    );
  }
  console.log(`smoke ok ${route} raw localized results`);
}

async function smokeNavigationServerActions(
  browser: Browser,
  route: string,
  expectedActions: number,
) {
  const page = await browser.newPage();
  const navigationActions: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "POST") return;
    if (!request.headers()["next-action"]) return;
    navigationActions.push(request.url());
  });

  try {
    const response = await page.goto(`${baseUrl}${route}`, {
      waitUntil: "networkidle",
    });
    if (!response || response.status() >= 400) {
      throw new Error(`${route} returned HTTP ${response?.status() ?? "no response"}`);
    }
    // Mount effects are the regression surface. Give deferred hydration work
    // one extra turn after networkidle before asserting the whole-page count.
    await page.waitForTimeout(500);
    if (new URL(route, baseUrl).pathname.endsWith("/explore")) {
      const exploreState = await page.evaluate(() => {
        const staticSnapshot = document.querySelector<HTMLElement>(
          "[data-explore-static-results]",
        );
        const interactive = document.querySelector<HTMLElement>(
          "[data-explore-interactive]",
        );
        return {
          staticHidden: staticSnapshot?.hidden ?? false,
          interactiveHidden: interactive?.hidden ?? true,
          interactiveCompanies:
            interactive?.querySelectorAll("[data-search-result-company]").length ?? 0,
          unhiddenHeadings: Array.from(document.querySelectorAll("h1")).filter(
            (heading) => !heading.closest("[hidden]"),
          ).length,
        };
      });
      if (
        !exploreState.staticHidden ||
        exploreState.interactiveHidden ||
        exploreState.interactiveCompanies === 0 ||
        exploreState.unhiddenHeadings !== 1
      ) {
        console.error("smoke explore hydration state", exploreState);
        throw new Error(
          `${route} did not swap its static snapshot for one accessible hydrated result tree`,
        );
      }
    }
    if (navigationActions.length !== expectedActions) {
      throw new Error(
        `${route} emitted ${navigationActions.length} navigation-time Next Server Action POST(s), expected ${expectedActions}`,
      );
    }
  } finally {
    await page.close();
  }
  console.log(`smoke ok ${route} ${expectedActions} navigation Server Action POST(s)`);
}

async function smokeSpaExploreNavigation(browser: Browser) {
  const page = await browser.newPage();
  const navigationActions: string[] = [];
  let captureNavigation = false;
  page.on("request", (request) => {
    if (!captureNavigation || request.method() !== "POST") return;
    if (!request.headers()["next-action"]) return;
    navigationActions.push(request.url());
  });

  try {
    const source = await page.goto(`${baseUrl}/en/company/stripe?q=python`, {
      waitUntil: "networkidle",
    });
    if (!source || source.status() >= 400) {
      throw new Error(
        `/en/company/stripe?q=python returned HTTP ${source?.status() ?? "no response"}`,
      );
    }

    // This is a real Next Link transition. Its first Explore render can still
    // observe the source route's ?q=python until HistoryUpdater commits the
    // target /explore URL, which previously caused a duplicate search action.
    const exploreLink = page.locator('a[aria-label="Explore"]').first();
    await exploreLink.waitFor({ state: "visible" });
    captureNavigation = true;
    await Promise.all([
      page.waitForURL(`${baseUrl}/en/explore`),
      exploreLink.click(),
    ]);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(500);

    const interactiveCompanies = await page.locator(
      "[data-explore-interactive]:not([hidden]) [data-search-result-company]",
    ).count();
    if (interactiveCompanies === 0 || navigationActions.length !== 0) {
      throw new Error(
        `SPA navigation to /en/explore rendered ${interactiveCompanies} companies and emitted ${navigationActions.length} Next Server Action POST(s)`,
      );
    }
  } finally {
    await page.close();
  }

  console.log("smoke ok query-bearing company -> unfiltered Explore SPA navigation 0 Server Actions");
}

async function smokeDiscoveryRoute(route: string, expectedStatus: number) {
  const response = await fetch(`${baseUrl}${route}`, { redirect: "manual" });
  if (response.status !== expectedStatus) {
    throw new Error(`${route} returned HTTP ${response.status}, expected ${expectedStatus}`);
  }
  console.log(`smoke ok ${route} ${expectedStatus}`);
}

async function smokeDiscoveryRedirect(route: string, location: string) {
  const response = await fetch(`${baseUrl}${route}`, { redirect: "manual" });
  if (response.status !== 308) {
    throw new Error(`${route} returned HTTP ${response.status}, expected 308`);
  }
  const actualLocation = response.headers.get("location");
  if (actualLocation !== location) {
    throw new Error(`${route} redirected to ${actualLocation}, expected ${location}`);
  }
  console.log(`smoke ok ${route} 308 ${location}`);
}

async function smokeMissingResource(
  browser: Browser,
  route: string,
  heading: string,
) {
  const page = await browser.newPage();
  try {
    const response = await page.goto(`${baseUrl}${route}`, {
      waitUntil: "networkidle",
    });
    if (response?.status() !== 404) {
      throw new Error(
        `${route} returned HTTP ${response?.status() ?? "no response"}, expected 404`,
      );
    }
    const headings = page.locator("h1");
    const renderedHeading = (await headings.textContent())?.trim();
    const robots = await page.locator('meta[name="robots"]').getAttribute("content");
    if (
      renderedHeading !== heading ||
      !robots?.includes("noindex") ||
      await headings.count() !== 1 ||
      await page.locator("main").count() !== 1
    ) {
      throw new Error(`${route} did not render its noindex localized recovery UI`);
    }
  } finally {
    await page.close();
  }

  const headResponse = await fetch(`${baseUrl}${route}`, {
    method: "HEAD",
    redirect: "manual",
  });
  if (headResponse.status !== 404) {
    throw new Error(`${route} HEAD returned HTTP ${headResponse.status}, expected 404`);
  }
  console.log(`smoke ok ${route} GET/HEAD 404`);
}

async function main() {
  const server = startServer();
  let browser: Browser | undefined;
  try {
    await waitForServer();
    browser = await chromium.launch();
    for (const route of discoveryNotFoundRoutes) {
      await smokeDiscoveryRoute(route, 404);
    }
    for (const [route, location] of discoveryRedirectRoutes) {
      await smokeDiscoveryRedirect(route, location);
    }
    for (const [route, heading] of missingResourceRoutes) {
      await smokeMissingResource(browser, route, heading);
    }
    for (const [route, heading] of localizedExploreRoutes) {
      await smokeExploreRawHtml(route, heading);
    }
    for (const [route, expectedActions] of navigationActionRoutes) {
      await smokeNavigationServerActions(browser, route, expectedActions);
    }
    await smokeSpaExploreNavigation(browser);
    for (const route of routes) {
      await smoke(browser, route);
    }
  } finally {
    await browser?.close();
    server.kill("SIGTERM");
  }
}

main().catch((error: unknown) => {
  logExternalError("error", { service: "external_http", operation: "smoke_built_app" }, error);
  process.exit(1);
});
