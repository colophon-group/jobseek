import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import { once } from "node:events";
import { chromium, type Page } from "playwright";
import { hashPassword } from "better-auth/crypto";
import { companyDocument, fixtureClient, fixtureDatabaseUrl, resetFixture, seedUser } from "./company-reference/fixture";
import { exerciseCanaryLifecycle, type CanaryLifecycleState } from "./company-reference/canary-lifecycle";
import { selectCanaryCompany, canaryCompanyRow } from "./company-reference/canary-picker";
import { navigateCanary, waitForCanaryOwnerShell } from "./company-reference/canary-navigation";
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
  const writeMode = process.env.COMPANY_REFERENCE_TEST_WRITE_MODE ?? "bridge";
  assert.ok(writeMode === "bridge" || writeMode === "reference", "Invalid browser fixture write mode");
  const doc = companyDocument(randomUUID(), `Reference fixture ${randomUUID().slice(0, 8)}`);
  const delayedDoc = companyDocument(randomUUID(), `Delayed reference fixture ${randomUUID().slice(0, 8)}`);
  const search = await startTypesenseFixture([doc, delayedDoc], { [delayedDoc.id]: 2000 });
  const appPort = await freePort(); const baseUrl = `http://127.0.0.1:${appPort}`;
  let child: ChildProcess | undefined; let page: Page | undefined;
  const browser = await chromium.launch({ headless: true });
  try {
    await resetFixture(sql, writeMode);
    const user = await seedUser(sql);
    await sql`INSERT INTO user_preferences (user_id, notifications_paused) VALUES (${user.id}, true) ON CONFLICT (user_id) DO UPDATE SET notifications_paused=true`;
    const password = `Fixture-${randomUUID()}-Aa1!`;
    // Hash with the real auth implementation and obtain the session through the real HTTP sign-in route.
    // The fixture is a verified test account; no auth override exists in app/runtime code.
    const hash = await hashPassword(password);
    await sql`INSERT INTO account (id, account_id, provider_id, user_id, password, updated_at)
      VALUES (${randomUUID()}, ${user.id}, 'credential', ${user.id}, ${hash}, now())`;
    assert.equal((await sql`SELECT id FROM company WHERE id=${doc.id}`).length, 0);
    assert.equal((await sql`SELECT id FROM company_reference WHERE id=${doc.id}`).length, 0);
    const inherited = Object.fromEntries(Object.entries(process.env).filter(([key]) => ["PATH", "HOME", "TMPDIR", "TEMP", "SystemRoot"].includes(key)));
    const env: NodeJS.ProcessEnv = { ...inherited, NODE_ENV: "development", NEXT_TELEMETRY_DISABLED: "1", DATABASE_URL: databaseUrl,
      COMPANY_REFERENCE_WRITE_MODE: writeMode, BETTER_AUTH_URL: baseUrl, BETTER_AUTH_SECRET: "fixture-auth-secret-never-used-outside-local-tests", TRUSTED_ORIGINS: baseUrl,
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
    await navigateCanary(page, "/en/watchlists");
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await page.waitForURL(/\/en\/watchlists\/[0-9a-f-]{36}/);
    const watchlistId = page.url().split("/").pop()!;
    browserPhase = "select_company";
    await selectCanaryCompany(page, doc.name, next => { browserPhase = next; });
    const dialog = page.getByRole("dialog");
    await page.getByText(doc.name, { exact: true }).waitFor();
    // UI optimistic state alone is insufficient: wait for the actual owner-scoped committed row.
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline && !(await sql`SELECT 1 FROM watchlist_company WHERE watchlist_id=${watchlistId} AND company_id=${doc.id}`).length) {
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    browserPhase = "persisted_sql";
    const persisted = await sql`SELECT w.user_id, w.alerts_enabled, COALESCE(w.filters->>'anyCompany', 'false') AS any_company, r.name, r.source, r.verified_at FROM watchlist w
      JOIN watchlist_company wc ON wc.watchlist_id=w.id JOIN company_reference r ON r.id=wc.company_id
      WHERE w.id=${watchlistId} AND wc.company_id=${doc.id}`;
    assert.equal(persisted.length, 1, "Picker save must commit a materialized reference");
    assert.equal(persisted[0].any_company, "false", "Company membership and scope must commit atomically before quick reload");
    assert.equal(persisted[0].user_id, user.id); assert.equal(persisted[0].source, "typesense"); assert.equal(persisted[0].alerts_enabled, false);
    assert.equal((await sql`SELECT id FROM company WHERE id=${doc.id}`).length, writeMode === "bridge" ? 1 : 0);
    browserPhase = "reload";
    await navigateCanary(page, page.url()); await page.getByRole("button", { name: `Remove ${doc.name}`, exact: true }).waitFor();
    assert.ok(search.requests.some(request => request.pathname.includes("/company/")), "Production Typesense SDK must query company fixture");
    browserPhase = "later_scope_during_provider_lookup";
    await page.getByRole("button", { name: "Company", exact: true }).click();
    await dialog.getByPlaceholder("Search companies...").fill(delayedDoc.name);
    await canaryCompanyRow(page, delayedDoc.name).click();
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    // This later edit must stay later than the in-flight membership request,
    // whose provider lookup intentionally takes longer than the filter debounce.
    await page.getByRole("button", { name: "Any company", exact: true }).click();
    const scopeDeadline = Date.now() + 15_000;
    let coherent = false;
    while (Date.now() < scopeDeadline) {
      const rows = await sql`SELECT w.filters->>'anyCompany' AS any_company,
        (SELECT count(*)::integer FROM watchlist_company wc WHERE wc.watchlist_id=w.id) AS membership_count
        FROM watchlist w WHERE w.id=${watchlistId} AND w.user_id=${user.id}`;
      if (rows[0]?.any_company === "true" && rows[0].membership_count === 2) { coherent = true; break; }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.ok(coherent, "Later scope edit must remain persisted after slower provider-backed membership save");
    assert.ok(search.requests.some(request => request.filter?.includes(delayedDoc.id) && request.delayMs === 2000), "Real production SDK must cross the delayed provider boundary");
    await navigateCanary(page, page.url());
    assert.equal(await page.getByRole("button", { name: "Company", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("button", { name: `Remove ${delayedDoc.name}`, exact: true }).count(), 0);
    assert.equal((await sql`SELECT 1 FROM watchlist_company WHERE watchlist_id=${watchlistId} AND company_id=${delayedDoc.id}`).length, 1);
    assert.equal((await sql`SELECT id FROM company WHERE id=${delayedDoc.id}`).length, process.env.COMPANY_REFERENCE_TEST_WRITE_MODE === "reference" ? 0 : 1);
    browserPhase = "complete_canary_lifecycle";
    await page.getByRole("button", { name: "Any company", exact: true }).click();
    await page.getByRole("button", { name: `Remove ${delayedDoc.name}`, exact: true }).click();
    const reducedDeadline = Date.now() + 15_000;
    while (Date.now() < reducedDeadline && (await sql`SELECT 1 FROM watchlist_company WHERE watchlist_id=${watchlistId}`).length !== 1) await new Promise(resolve => setTimeout(resolve, 100));
    const [owned] = await sql`SELECT title FROM watchlist WHERE id=${watchlistId} AND user_id=${user.id}`;
    const state: CanaryLifecycleState = { titles: [owned.title], company: doc, starTouched: false };
    const lifecycle = await exerciseCanaryLifecycle({ page, sql, userId: user.id, email: user.email, password,
      origin: baseUrl, watchlistId, state, tailPacing: { target: "local" }, onPhase: next => { browserPhase = next; }, createAnonymousContext: () => browser.newContext({ baseURL: baseUrl }) });
    browserPhase = "cleanup_owner_shell";
    await waitForCanaryOwnerShell(page, state.titles[state.titles.length - 1]);
    browserPhase = "cleanup_delete_trigger";
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
    await page.waitForURL(/\/en\/watchlists$/);
    assert.equal((await sql`SELECT 1 FROM watchlist WHERE id=${watchlistId} AND user_id=${user.id}`).length, 0);
    assert.equal((await sql`SELECT 1 FROM company_reference WHERE id=${doc.id}`).length, 1, "Cleanup must retain shared durable reference");
    console.log(JSON.stringify({ contract: "company_reference_authenticated_browser", outcome: "passed", absentLegacyBefore: true, absentReferenceBefore: true,
      authenticatedMutation: true, committedMembership: true, persistedReload: true, laterScopeDuringLookup: true, ...lifecycle, notificationsEnabled: false, scopedCleanup: true, writeMode, legacyRowsAfterSelection: writeMode === "bridge" ? 1 : 0 }));
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
