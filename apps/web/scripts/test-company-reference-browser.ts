import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import { once } from "node:events";
import { chromium, type Page } from "playwright";
import { hashPassword } from "better-auth/crypto";
import { companyDocument, fixtureClient, fixtureDatabaseUrl, resetFixture, seedUser } from "./company-reference/fixture";
import { logExternalError } from "../src/lib/safe-external-error";
import { startTypesenseFixture } from "./company-reference/typesense-fixture";

let browserPhase = "fixture";

async function freePort(): Promise<number> {
  const server = createServer(); server.listen(0, "127.0.0.1"); await once(server, "listening");
  const port = (server.address() as { port: number }).port; await new Promise<void>(resolve => server.close(() => resolve())); return port;
}
async function waitForApp(url: string, child: ChildProcess) {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error("Local Next app exited before readiness");
    try { if ((await fetch(`${url}/api/auth/get-session`)).status < 500) return; } catch { /* booting */ }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error("Local Next app did not become ready");
}

async function main() {
  // This runner never loads .env files. Its app child receives only isolated dependency URLs.
  const databaseUrl = fixtureDatabaseUrl(); const sql = fixtureClient();
  const doc = companyDocument(randomUUID(), `Reference fixture ${randomUUID().slice(0, 8)}`);
  const search = await startTypesenseFixture([doc]);
  const appPort = await freePort(); const baseUrl = `http://127.0.0.1:${appPort}`;
  let child: ChildProcess | undefined; let page: Page | undefined;
  const browser = await chromium.launch({ headless: true });
  try {
    await resetFixture(sql);
    const user = await seedUser(sql); const password = `Fixture-${randomUUID()}-Aa1!`;
    // Hash with the real auth implementation and obtain the session through the real HTTP sign-in route.
    // The fixture is a verified test account; no auth override exists in app/runtime code.
    const hash = await hashPassword(password);
    await sql`INSERT INTO account (id, account_id, provider_id, user_id, password, updated_at)
      VALUES (${randomUUID()}, ${user.id}, 'credential', ${user.id}, ${hash}, now())`;
    assert.equal((await sql`SELECT id FROM company WHERE id=${doc.id}`).length, 0);
    assert.equal((await sql`SELECT id FROM company_reference WHERE id=${doc.id}`).length, 0);
    const inherited = Object.fromEntries(Object.entries(process.env).filter(([key]) => ["PATH", "HOME", "TMPDIR", "TEMP", "SystemRoot"].includes(key)));
    const env: NodeJS.ProcessEnv = { ...inherited, NODE_ENV: "development", NEXT_TELEMETRY_DISABLED: "1", DATABASE_URL: databaseUrl,
      BETTER_AUTH_URL: baseUrl, BETTER_AUTH_SECRET: "fixture-auth-secret-never-used-outside-local-tests", TRUSTED_ORIGINS: baseUrl,
      TYPESENSE_HOST: "127.0.0.1", TYPESENSE_PORT: String(search.port), TYPESENSE_PROTOCOL: "http", TYPESENSE_SEARCH_KEY: "fixture-read-key",
      NEXT_PUBLIC_TYPESENSE_HOST: "127.0.0.1", NEXT_PUBLIC_TYPESENSE_PORT: String(search.port), NEXT_PUBLIC_TYPESENSE_PROTOCOL: "http", NEXT_PUBLIC_TYPESENSE_SEARCH_KEY: "fixture-read-key",
      UPSTASH_REDIS_REST_URL: `http://127.0.0.1:${search.port}/redis`, UPSTASH_REDIS_REST_TOKEN: "fixture-only", };
    browserPhase = "app_startup";
    child = spawn("pnpm", ["exec", "next", "dev", "--hostname", "127.0.0.1", "--port", String(appPort)], { env, detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"] });
    // Do not dump auth-bearing app logs. Retain only a bounded diagnostic tail and redact on failure.
    let logs = ""; const append = (chunk: Buffer) => { logs = (logs + chunk.toString()).slice(-6000); };
    child.stdout?.on("data", append); child.stderr?.on("data", append);
    await waitForApp(baseUrl, child);
    const context = await browser.newContext({ baseURL: baseUrl });
    browserPhase = "sign_in";
    const signedIn = await context.request.post("/api/auth/sign-in/email", { data: { email: user.email, password }, headers: { origin: baseUrl } });
    assert.equal(signedIn.status(), 200, "Real fixture sign-in must succeed");
    const session = await context.request.get("/api/auth/get-session");
    assert.equal((await session.json()).user.id, user.id, "Request-derived session must match dedicated fixture identity");
    page = await context.newPage(); page.setDefaultTimeout(45_000);
    browserPhase = "create_watchlist";
    await page.goto("/en/watchlists");
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await page.waitForURL(/\/en\/watchlists\/[0-9a-f-]{36}/);
    const watchlistId = page.url().split("/").pop()!;
    browserPhase = "select_company";
    await page.getByRole("button", { name: "Any company", exact: true }).click();
    await page.getByRole("button", { name: "Company", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByPlaceholder("Search companies...").fill(doc.name);
    await dialog.getByRole("button", { name: new RegExp(doc.name) }).click();
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await page.getByText(doc.name, { exact: true }).waitFor();
    // UI optimistic state alone is insufficient: wait for the actual owner-scoped committed row.
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline && !(await sql`SELECT 1 FROM watchlist_company WHERE watchlist_id=${watchlistId} AND company_id=${doc.id}`).length) {
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    browserPhase = "persisted_sql";
    const persisted = await sql`SELECT w.user_id, w.alerts_enabled, r.name, r.source, r.verified_at FROM watchlist w
      JOIN watchlist_company wc ON wc.watchlist_id=w.id JOIN company_reference r ON r.id=wc.company_id
      WHERE w.id=${watchlistId} AND wc.company_id=${doc.id}`;
    assert.equal(persisted.length, 1, "Picker save must commit a materialized reference");
    assert.equal(persisted[0].user_id, user.id); assert.equal(persisted[0].source, "typesense"); assert.equal(persisted[0].alerts_enabled, false);
    browserPhase = "reload";
    await page.reload(); await page.getByRole("button", { name: `Remove ${doc.name}`, exact: true }).waitFor();
    assert.ok(search.requests.some(request => request.pathname.includes("/company/")), "Production Typesense SDK must query company fixture");
    browserPhase = "scoped_cleanup";
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
    await page.waitForURL(/\/en\/watchlists$/);
    assert.equal((await sql`SELECT 1 FROM watchlist WHERE id=${watchlistId} AND user_id=${user.id}`).length, 0);
    assert.equal((await sql`SELECT 1 FROM company_reference WHERE id=${doc.id}`).length, 1, "Cleanup must retain shared durable reference");
    console.log(JSON.stringify({ contract: "company_reference_authenticated_browser", outcome: "passed", absentLegacyBefore: true, absentReferenceBefore: true,
      authenticatedMutation: true, committedMembership: true, persistedReload: true, notificationsEnabled: false, scopedCleanup: true }));
    await context.close();
  } catch (error) {
    await page?.screenshot({ path: "/tmp/jobseek-company-reference-browser-failure.png", fullPage: true });
    throw error;
  } finally {
    if (child?.pid) {
      if (process.platform === "win32") child.kill("SIGTERM");
      else { try { process.kill(-child.pid, "SIGTERM"); } catch { /* already exited */ } }
    }
    await browser.close(); search.server.close(); await sql.end({ timeout: 5 });
  }
}
void main().catch((error: unknown) => {
  // Avoid printing request/session/provider material. Detailed failures remain reproducible locally.
  console.error(JSON.stringify({ contract: "company_reference_authenticated_browser", outcome: "failed", phase: browserPhase }));
  logExternalError("error", { service: "external_http", operation: "company_reference_browser_contract" }, error); process.exitCode = 1;
});
