import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { parse } from "dotenv";
import postgres from "postgres";
import { Client } from "typesense";
import { chromium, type Page } from "playwright";
import { logExternalError } from "../src/lib/safe-external-error";

const CONTRACT = "company_reference_staged_canary";
let phase = "configuration";
function required(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`Required dedicated canary configuration missing: ${name}`);
  return value;
}
function check(condition: unknown, code: string): asserts condition {
  if (!condition) throw Object.assign(new Error(code), { code });
}
function pause(ms = 100) { return new Promise(resolve => setTimeout(resolve, ms)); }

async function main() {
  const base = new URL(required("DEPLOYMENT_URL"));
  check(base.protocol === "https:" && base.hostname.endsWith(".vercel.app") && !base.username && !base.password && base.pathname === "/", "INVALID_STAGED_TARGET");
  const userId = required("COMPANY_REFERENCE_CANARY_USER_ID");
  const email = required("COMPANY_REFERENCE_CANARY_EMAIL");
  const password = required("COMPANY_REFERENCE_CANARY_PASSWORD");
  const bypass = required("VERCEL_AUTOMATION_BYPASS_SECRET");
  const sql = postgres(required("DATABASE_URL_UNPOOLED"), { max: 1, prepare: false,
    connection: { application_name: "jobseek-company-reference-canary-read-only", default_transaction_read_only: true, statement_timeout: 10_000 } });
  const values = parse(await readFile(new URL("../../../.vercel/.env.production.local", import.meta.url), "utf8"));
  const search = new Client({ nodes: [{ host: values.TYPESENSE_HOST, port: Number(values.TYPESENSE_PORT), protocol: values.TYPESENSE_PROTOCOL }],
    apiKey: values.TYPESENSE_SEARCH_KEY, logLevel: "silent", connectionTimeoutSeconds: 5 });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ baseURL: base.origin });
  // Scope the deployment bypass to this exact verified first-party origin.
  // Global browser headers would also reach analytics, images, and external links.
  await context.route("**/*", async route => {
    if (new URL(route.request().url()).origin !== base.origin) return route.continue();
    await route.continue({ headers: { ...route.request().headers(), "x-vercel-protection-bypass": bypass } });
  });
  let page: Page | undefined; let watchlistId: string | undefined;
  const title = `company-reference-canary:${randomUUID()}`;
  let failure: unknown; let failurePhase: string | undefined;
  try {
    phase = "dedicated_identity_preflight";
    const identity = await sql`SELECT id, email, email_verified FROM "user" WHERE id=${userId}`;
    check(identity.length === 1 && identity[0].email === email && identity[0].email_verified, "DEDICATED_IDENTITY_NOT_VERIFIED");
    const [capacity] = await sql`SELECT count(*)::integer AS value FROM watchlist WHERE user_id=${userId}`;
    check(capacity.value < 10, "CANARY_ACCOUNT_CAPACITY_EXHAUSTED");
    const missing = await sql`SELECT count(*)::integer AS value FROM (
      SELECT wc.company_id FROM watchlist_company wc LEFT JOIN company_reference cr ON cr.id=wc.company_id WHERE cr.id IS NULL
      UNION ALL SELECT fc.company_id FROM followed_company fc LEFT JOIN company_reference cr ON cr.id=fc.company_id WHERE cr.id IS NULL
    ) dangling`;
    check(missing[0].value === 0, "PERSISTED_SELECTION_REFERENCE_MISSING");

    phase = "novel_catalogue_identity";
    let doc: { id: string; name: string; slug: string } | undefined;
    for (let pageNumber = 1; pageNumber <= 30 && !doc; pageNumber++) {
      const result = await search.collections<{ id: string; name: string; slug: string }>("company").documents().search({ q: "*", query_by: "name", filter_by: "active_posting_count:>0", per_page: 250, page: pageNumber });
      const documents = (result.hits ?? []).map(hit => hit.document);
      if (!documents.length) break;
      const ids = documents.map(company => company.id);
      const existing = await sql`SELECT id FROM company_reference WHERE id=ANY(${ids}::uuid[]) UNION SELECT id FROM company WHERE id=ANY(${ids}::uuid[])`;
      const seen = new Set(existing.map(row => row.id)); doc = documents.find(company => !seen.has(company.id));
    }
    // Existing-reference success does not qualify as first-use verification. Never delete data to force this arm.
    check(doc, "FIRST_USE_CATALOGUE_FIXTURE_UNAVAILABLE");
    check(/^[0-9a-f-]{36}$/i.test(doc.id) && doc.name.length <= 300 && doc.slug.length <= 100, "INVALID_CANONICAL_FIXTURE");

    phase = "authenticated_request_identity";
    const signedIn = await context.request.post("/api/auth/sign-in/email", { data: { email, password }, headers: { origin: base.origin, "x-vercel-protection-bypass": bypass } });
    check(signedIn.status() === 200, "CANARY_SIGN_IN_FAILED");
    const session = await context.request.get("/api/auth/get-session", { headers: { "x-vercel-protection-bypass": bypass } });
    const sessionBody = await session.json();
    check(session.status() === 200 && sessionBody.user?.id === userId, "CANARY_SESSION_IDENTITY_MISMATCH");

    phase = "create_scoped_watchlist";
    page = await context.newPage(); page.setDefaultTimeout(30_000);
    await page.goto(`/en/watchlists?title=${encodeURIComponent(title)}`);
    await page.waitForURL(/\/en\/watchlists\/[0-9a-f-]{36}/);
    watchlistId = page.url().split("/").pop()!;
    const owned = await sql`SELECT user_id, title, alerts_enabled FROM watchlist WHERE id=${watchlistId}`;
    check(owned.length === 1 && owned[0].user_id === userId && owned[0].title === title && !owned[0].alerts_enabled, "CANARY_NAMESPACE_OWNERSHIP_MISMATCH");

    phase = "first_use_picker_save";
    const stillAbsent = await sql`SELECT id FROM company_reference WHERE id=${doc.id} UNION SELECT id FROM company WHERE id=${doc.id}`;
    check(stillAbsent.length === 0, "FIRST_USE_FIXTURE_CHANGED");
    await page.getByRole("button", { name: "Any company", exact: true }).click();
    await page.getByRole("button", { name: "Company", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByPlaceholder("Search companies...").fill(doc.name);
    await dialog.getByRole("button").filter({ hasText: doc.name }).first().click();
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    const deadline = Date.now() + 15_000;
    let persisted = false;
    while (Date.now() < deadline) {
      const row = await sql`SELECT r.source, r.verified_at, w.alerts_enabled FROM watchlist_company wc
        JOIN watchlist w ON w.id=wc.watchlist_id JOIN company_reference r ON r.id=wc.company_id
        WHERE wc.watchlist_id=${watchlistId} AND wc.company_id=${doc.id} AND w.user_id=${userId} AND w.title=${title}`;
      if (row.length === 1 && row[0].source === "typesense" && row[0].verified_at && !row[0].alerts_enabled) { persisted = true; break; }
      await pause();
    }
    check(persisted, "FIRST_USE_SELECTION_NOT_COMMITTED");
    phase = "persisted_reload";
    await page.reload(); await page.getByText(doc.name, { exact: true }).waitFor();
    const dangling = await sql`SELECT 1 FROM watchlist_company wc LEFT JOIN company_reference r ON r.id=wc.company_id WHERE wc.watchlist_id=${watchlistId} AND r.id IS NULL`;
    check(dangling.length === 0, "CANARY_SELECTION_REFERENCE_MISSING");
  } catch (error) { failure = error; failurePhase = phase; }
  finally {
    if (watchlistId) {
      try {
        phase = "scoped_cleanup";
        const owned = await sql`SELECT user_id, title FROM watchlist WHERE id=${watchlistId}`;
        check(owned.length === 1 && owned[0].user_id === userId && owned[0].title === title, "CLEANUP_OWNERSHIP_MISMATCH");
        // Delete through the production authenticated action, after exact owner + fresh namespace checks.
        await page!.goto(`/en/watchlists/${watchlistId}`);
        await page!.getByRole("button", { name: "Delete", exact: true }).click();
        await page!.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
        await page!.waitForURL(/\/en\/watchlists$/);
        const retained = await sql`SELECT 1 FROM watchlist WHERE id=${watchlistId}`;
        check(retained.length === 0, "CANARY_CLEANUP_FAILED");
      } catch (error) { if (!failure) { failure = error; failurePhase = phase; } }
    }
    await context.close(); await browser.close(); await sql.end({ timeout: 5 });
  }
  if (failure) { phase = failurePhase!; throw failure; }
  console.log(JSON.stringify({ contract: CONTRACT, outcome: "passed", firstUse: true, realRequestIdentity: true, committedSelection: true, persistedReload: true, scopedCleanup: true }));
}
void main().catch(error => {
  console.error(JSON.stringify({ contract: CONTRACT, outcome: "failed", phase }));
  logExternalError("error", { service: "external_http", operation: "company_reference_staged_canary" }, error);
  process.exitCode = 1;
});
