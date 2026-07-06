import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { chromium, type Browser } from "playwright";

const port = Number(process.env.SMOKE_PORT ?? "3100");
const baseUrl = `http://127.0.0.1:${port}`;
const routes = ["/en", "/en/explore", "/en/companies/request"];

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

async function main() {
  const server = startServer();
  let browser: Browser | undefined;
  try {
    await waitForServer();
    browser = await chromium.launch();
    for (const route of routes) {
      await smoke(browser, route);
    }
  } finally {
    await browser?.close();
    server.kill("SIGTERM");
  }
}

main().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});
