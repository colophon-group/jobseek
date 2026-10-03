import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { parse } from "dotenv";
import postgres from "postgres";
import { Client } from "typesense";
import { chromium, type Page } from "playwright";
import { exerciseCanaryLifecycle, restoreCanaryStar, requireCanaryCleanup, readCanaryReadinessState, type CanaryLifecycleState } from "./company-reference/canary-lifecycle";
import { openCanaryAccess, resolveCanaryTarget, reattestPublicCanaryIdentity, validateCanaryDeploymentIdentity, type CanaryDeploymentIdentity } from "./company-reference/canary-target";
import { selectCanaryCompany, canaryFailureKind } from "./company-reference/canary-picker";
import { navigateCanary, waitForCanaryOwnerShell, type CanaryOwnerEvidence } from "./company-reference/canary-navigation";
import { logExternalError } from "../src/lib/safe-external-error";

let contract = "company_reference_staged_canary";
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
  const target = resolveCanaryTarget(required("DEPLOYMENT_URL"), process.env.COMPANY_REFERENCE_CANARY_TARGET);
  const base = target.base;
  contract = target.kind === "production" ? "company_reference_production_canary" : "company_reference_staged_canary";
  const expectedIdentity: CanaryDeploymentIdentity | undefined = target.kind === "production" ? validateCanaryDeploymentIdentity({
    sha: required("EXPECTED_SHA"), id: required("EXPECTED_DEPLOYMENT_ID"), url: required("EXPECTED_DEPLOYMENT_URL"),
  }) : undefined;
  const userId = required("COMPANY_REFERENCE_CANARY_USER_ID");
  const email = required("COMPANY_REFERENCE_CANARY_EMAIL");
  const password = required("COMPANY_REFERENCE_CANARY_PASSWORD");
  const bypass = target.kind === "staged" ? required("VERCEL_AUTOMATION_BYPASS_SECRET") : undefined;
  const sql = postgres(required("DATABASE_URL_UNPOOLED"), { max: 1, prepare: false,
    connection: { application_name: "jobseek-company-reference-canary-read-only", default_transaction_read_only: true, statement_timeout: 10_000 } });
  const values = parse(await readFile(new URL("../../../.vercel/.env.production.local", import.meta.url), "utf8"));
  const writeMode = values.COMPANY_REFERENCE_WRITE_MODE ?? "bridge";
  check(writeMode === "bridge" || writeMode === "reference", "INVALID_PRODUCTION_WRITE_MODE");
  const search = new Client({ nodes: [{ host: values.TYPESENSE_HOST, port: Number(values.TYPESENSE_PORT), protocol: values.TYPESENSE_PROTOCOL }],
    apiKey: values.TYPESENSE_SEARCH_KEY, logLevel: "silent", connectionTimeoutSeconds: 5 });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ baseURL: base.origin });
  let page: Page | undefined; let watchlistId: string | undefined;
  let lifecycleState: CanaryLifecycleState | undefined;
  let lifecycleProof: Awaited<ReturnType<typeof exerciseCanaryLifecycle>> | undefined;
  const onEvidence = (evidence: CanaryOwnerEvidence) => console.log(JSON.stringify({ contract, outcome: "readiness", ...evidence }));
  const readReadinessState = (ownerPage: Page, id: string, expectedTitle: string, readiness: CanaryOwnerEvidence["readiness"]) => readCanaryReadinessState(ownerPage, sql, userId, id, expectedTitle, readiness !== "ready");
  const title = `company-reference-canary:${randomUUID()}`;
  let failure: unknown; let failurePhase: string | undefined;
  let sessionMayExist = false;
  const cleanupProof = { recovered: false, deleted: 0, residual: null as number | null, starRestored: false, sessionClosed: false };
  try {
    phase = "deployment_access";
    await openCanaryAccess(context, target, bypass);
    phase = "dedicated_identity_preflight";
    const identity = await sql`SELECT id, email, email_verified FROM "user" WHERE id=${userId}`;
    check(identity.length === 1 && identity[0].email === email && identity[0].email_verified, "DEDICATED_IDENTITY_NOT_VERIFIED");
    const [capacity] = await sql`SELECT count(*)::integer AS value FROM watchlist WHERE user_id=${userId}`;
    check(capacity.value <= 8, "CANARY_ACCOUNT_CAPACITY_EXHAUSTED");
    const [policy] = await sql`SELECT notifications_paused FROM user_preferences WHERE user_id=${userId}`;
    check(policy?.notifications_paused === true, "CANARY_NOTIFICATIONS_NOT_PAUSED");
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
      const seen = new Set(existing.map(row => row.id));
      for (const candidate of documents.filter(company => !seen.has(company.id))) {
        const exact = await search.collections<{ id: string; name: string; slug: string }>("company").documents().search({
          q: candidate.name, query_by: "name", prefix: false, num_typos: 0, per_page: 250,
        });
        if (exact.found <= 250 && (exact.hits ?? []).filter(hit => hit.document.name === candidate.name).length === 1) { doc = candidate; break; }
      }
    }
    // Existing-reference success does not qualify as first-use verification. Never delete data to force this arm.
    check(doc, "FIRST_USE_CATALOGUE_FIXTURE_UNAVAILABLE");
    check(/^[0-9a-f-]{36}$/i.test(doc.id) && doc.name.length <= 300 && doc.slug.length <= 100, "INVALID_CANONICAL_FIXTURE");

    lifecycleState = { titles: [title], company: doc, starTouched: false, referenceCoverage: "first_use" };
    if (expectedIdentity) {
      phase = "production_identity_before_lifecycle";
      await reattestPublicCanaryIdentity(expectedIdentity);
    }
    phase = "authenticated_request_identity";
    sessionMayExist = true; // A timed-out sign-in can already have committed a session.
    const signedIn = await context.request.post("/api/auth/sign-in/email", { data: { email, password }, headers: { origin: base.origin }, maxRedirects: 0 });
    check(signedIn.status() === 200, "CANARY_SIGN_IN_FAILED");
    const session = await context.request.get("/api/auth/get-session", { maxRedirects: 0 });
    const sessionBody = await session.json();
    check(session.status() === 200 && sessionBody.user?.id === userId, "CANARY_SESSION_IDENTITY_MISMATCH");

    phase = "create_scoped_watchlist";
    page = await context.newPage(); page.setDefaultTimeout(30_000);
    // The title query starts a create after hydration, so this navigation must not be replayed.
    const creationNavigation = await page.goto(`/en/watchlists?title=${encodeURIComponent(title)}`);
    check(creationNavigation?.status() === 200, "CANARY_CREATE_NAVIGATION_FAILED");
    await page.waitForURL(/\/en\/watchlists\/[0-9a-f-]{36}/);
    watchlistId = page.url().split("/").pop()!;
    const owned = await sql`SELECT user_id, title, alerts_enabled FROM watchlist WHERE id=${watchlistId}`;
    check(owned.length === 1 && owned[0].user_id === userId && owned[0].title === title && !owned[0].alerts_enabled, "CANARY_NAMESPACE_OWNERSHIP_MISMATCH");

    phase = "first_use_picker_save";
    const stillAbsent = await sql`SELECT id FROM company_reference WHERE id=${doc.id} UNION SELECT id FROM company WHERE id=${doc.id}`;
    check(stillAbsent.length === 0, "FIRST_USE_FIXTURE_CHANGED");
    await selectCanaryCompany(page, doc.name, next => { phase = next; });
    phase = "picker_persistence_check";
    const deadline = Date.now() + 15_000;
    let persisted = false;
    while (Date.now() < deadline) {
      const row = await sql`SELECT r.source, r.verified_at, w.alerts_enabled, COALESCE(w.filters->>'anyCompany', 'false') AS any_company,
        (SELECT count(*)::integer FROM watchlist_company all_wc WHERE all_wc.watchlist_id=w.id) AS membership_count FROM watchlist_company wc
        JOIN watchlist w ON w.id=wc.watchlist_id JOIN company_reference r ON r.id=wc.company_id
        WHERE wc.watchlist_id=${watchlistId} AND wc.company_id=${doc.id} AND w.user_id=${userId} AND w.title=${title}`;
      if (row.length === 1 && row[0].source === "typesense" && row[0].verified_at && !row[0].alerts_enabled && row[0].any_company === "false" && row[0].membership_count === 1) { persisted = true; break; }
      await pause();
    }
    check(persisted, "FIRST_USE_SELECTION_NOT_COMMITTED");
    const legacy = await sql`SELECT 1 FROM company WHERE id=${doc.id}`;
    check(legacy.length === (writeMode === "bridge" ? 1 : 0), "PRODUCTION_WRITE_MODE_CONTRACT_MISMATCH");
    phase = "persisted_reload";
    await navigateCanary(page, page.url()); await page.getByRole("button", { name: `Remove ${doc.name}`, exact: true }).waitFor();
    const dangling = await sql`SELECT 1 FROM watchlist_company wc LEFT JOIN company_reference r ON r.id=wc.company_id WHERE wc.watchlist_id=${watchlistId} AND r.id IS NULL`;
    check(dangling.length === 0, "CANARY_SELECTION_REFERENCE_MISSING");
    phase = "complete_authenticated_lifecycle";
    lifecycleProof = await exerciseCanaryLifecycle({ page, sql, userId, email, password, origin: base.origin,
      watchlistId, state: lifecycleState, onEvidence, readReadinessState, onPhase: next => { phase = next; }, createAnonymousContext: async () => {
        const anonymous = await browser.newContext({ baseURL: base.origin });
        try { await openCanaryAccess(anonymous, target, bypass); return anonymous; }
        catch (error) { await anonymous.close(); throw error; }
      } });
  } catch (error) { failure = error; failurePhase = phase; }
  finally {
    // Restore a dedicated account's exact pre-existing star state before deleting owned fixtures.
    if (page && lifecycleState) {
      try { await restoreCanaryStar(page, sql, userId, lifecycleState); cleanupProof.starRestored = true; }
      catch (error) { if (!failure) { failure = error; failurePhase = "star_cleanup"; } }
    }
    // Recover committed create/copy even if navigation failed before recording IDs.
    const titles = lifecycleState?.titles ?? [title];
    let cleanupIds: string[] = [];
    try {
      const created = await sql`SELECT id FROM watchlist WHERE user_id=${userId} AND title=ANY(${titles}::text[])`;
      cleanupProof.residual = created.length;
      check(created.length <= 2, "CLEANUP_NAMESPACE_AMBIGUOUS");
      check(!watchlistId || created.some(row => row.id === watchlistId), "CLEANUP_IDENTITY_MISMATCH");
      cleanupIds = created.map(row => row.id); cleanupProof.recovered = true;
    } catch (error) { if (!failure) { failure = error; failurePhase = "cleanup_recovery"; } }
    for (const cleanupId of cleanupIds) {
      try {
        phase = "cleanup_ownership_preflight";
        const owned = await sql`SELECT user_id, title FROM watchlist WHERE id=${cleanupId}`;
        check(owned.length === 1 && owned[0].user_id === userId && titles.includes(owned[0].title), "CLEANUP_OWNERSHIP_MISMATCH");
        page ??= await context.newPage();
        phase = "cleanup_navigation";
        await navigateCanary(page, `/en/watchlists/${cleanupId}`);
        phase = "cleanup_request_session";
        const cleanupSession = await context.request.get("/api/auth/get-session", { maxRedirects: 0 });
        check(cleanupSession.status() === 200 && (await cleanupSession.json()).user?.id === userId, "CLEANUP_SESSION_IDENTITY_MISMATCH");
        phase = "cleanup_owner_shell";
        await waitForCanaryOwnerShell(page, owned[0].title, { stage: "cleanup", expectedPath: `/en/watchlists/${cleanupId}`, expectedOrigin: base.origin, referenceCoverage: lifecycleState?.referenceCoverage, onPhase: next => { phase = next; }, onEvidence, readState: readiness => readReadinessState(page!, cleanupId, owned[0].title, readiness) });
        phase = "cleanup_delete_trigger";
        await page.getByRole("button", { name: "Delete", exact: true }).click();
        phase = "cleanup_delete_confirmation";
        await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
        phase = "cleanup_delete_persistence";
        await page.waitForURL(/\/en\/watchlists$/);
        check((await sql`SELECT 1 FROM watchlist WHERE id=${cleanupId}`).length === 0, "CANARY_CLEANUP_FAILED");
        cleanupProof.deleted++;
      } catch (error) { if (!failure) { failure = error; failurePhase = phase; } }
    }
    try {
      const [remaining] = await sql`SELECT count(*)::integer AS value FROM watchlist WHERE user_id=${userId} AND title=ANY(${titles}::text[])`;
      cleanupProof.residual = remaining.value;
      check(remaining.value === 0, "CANARY_CLEANUP_RESIDUAL");
    } catch (error) { if (!failure) { failure = error; failurePhase = "cleanup_residual_check"; } }
    if (sessionMayExist) {
      try {
        const signedOut = await context.request.post("/api/auth/sign-out", { data: {}, headers: { origin: base.origin }, maxRedirects: 0 });
        check(signedOut.status() === 200, "CANARY_SESSION_CLEANUP_FAILED"); cleanupProof.sessionClosed = true;
      } catch (error) { if (!failure) { failure = error; failurePhase = "session_cleanup"; } }
    }
    console.log(JSON.stringify({ contract, outcome: "cleanup", ...cleanupProof }));
    await context.close(); await browser.close(); await sql.end({ timeout: 5 });
  }
  if (failure) { phase = failurePhase!; throw failure; }
  phase = "cleanup_final_proof";
  requireCanaryCleanup(cleanupProof);
  if (expectedIdentity) {
    phase = "production_identity_after_cleanup";
    await reattestPublicCanaryIdentity(expectedIdentity);
  }
  console.log(JSON.stringify({ contract, target: target.kind, aliasIdentityBefore: Boolean(expectedIdentity), aliasIdentityAfter: Boolean(expectedIdentity), outcome: "passed", firstUse: true, realRequestIdentity: true, committedSelection: true, persistedReload: true, scopedCleanup: true, ...lifecycleProof, writeMode, legacyRowsAfterSelection: writeMode === "bridge" ? 1 : 0 }));
}
void main().catch(error => {
  // This tested classifier returns only fixed categories; it never returns error text or codes.
  // eslint-disable-next-line safe-client-logging/no-raw-errors
  console.error(JSON.stringify({ contract, outcome: "failed", phase, failureKind: canaryFailureKind(error) }));
  logExternalError("error", { service: "external_http", operation: contract }, error);
  process.exitCode = 1;
});
