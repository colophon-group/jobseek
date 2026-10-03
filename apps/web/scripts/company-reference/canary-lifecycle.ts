import type { BrowserContext, Page } from "playwright";
import type { Sql } from "postgres";
import { navigateCanary, waitForCanaryOwnerShell, canaryRouteReusable, settleCanaryTail, type CanaryTailPolicy, type CanaryOwnerEvidence, type CanaryReadinessState } from "./canary-navigation";

export type CanaryLifecycleState = {
  titles: string[];
  company: { id: string; name: string; slug: string };
  initialStarred?: boolean;
  starTouched: boolean;
  referenceCoverage?: "first_use" | "existing_reference";
};
function check(condition: unknown, code: string): asserts condition {
  if (!condition) throw Object.assign(new Error(code), { code });
}
async function until(probe: () => Promise<boolean>, code: string) {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) { if (await probe()) return; await new Promise(resolve => setTimeout(resolve, 100)); }
  throw Object.assign(new Error(code), { code });
}

/** Bounded diagnostic reads cancel the sibling SELECT on every error path. */
export async function readCanaryReadinessState(ownerPage: Page, sql: Sql, userId: string, id: string, expectedTitle: string, includeSession = false): Promise<CanaryReadinessState> {
  const query = sql`SELECT (w.title=${expectedTitle}) AS title_matches,
    (SELECT count(*)::integer FROM watchlist_company wc WHERE wc.watchlist_id=w.id) AS membership_count,
    COALESCE(w.filters->>'anyCompany', 'false') AS any_company
    FROM watchlist w WHERE w.id=${id} AND w.user_id=${userId}`;
  const timer = setTimeout(() => { try { query.cancel(); } catch { /* Diagnostics cannot replace the original failure. */ } }, 1_800);
  try {
    const [rows, response] = await Promise.all([query, includeSession ? ownerPage.context().request.get("/api/auth/get-session", { maxRedirects: 0, timeout: 1_800 }) : Promise.resolve(null)]);
    const text = response ? await response.text() : null;
    const session = text !== null && text.length <= 65_536 ? JSON.parse(text) as { user?: { id?: unknown } } : null;
    return { persistedTitleMatches: rows[0]?.title_matches ?? null, companyMembershipCount: rows[0]?.membership_count ?? null,
      anyCompany: rows[0]?.any_company === "true" ? true : rows[0]?.any_company === "false" ? false : null,
      sessionStatus: response?.status() ?? null, sessionIdentityMatches: session ? session.user?.id === userId : null };
  } catch (error) {
    // Promise.all does not cancel a SELECT when its sibling session GET fails.
    // Release the sole diagnostic connection before cleanup uses it.
    try { query.cancel(); } catch { /* Preserve the original diagnostic error. */ }
    throw error;
  } finally { clearTimeout(timer); }
}

/** PPR may retain a hidden auth subtree: require one visible complete form, never an arbitrary first match. */
export async function signInCanaryAccount(page: Page, email: string, password: string, onPhase?: (phase: string) => void) {
  onPhase?.("canary_clone_sign_in_form");
  const form = page.locator("form").filter({ visible: true })
    .filter({ has: page.getByLabel("Email or username", { exact: true }).filter({ visible: true }) })
    .filter({ has: page.getByLabel("Password", { exact: true }).filter({ visible: true }) })
    .filter({ has: page.getByRole("button", { name: "Sign in", exact: true }) });
  await until(async () => (await form.count()) === 1, "CANARY_SIGN_IN_FORM_AMBIGUOUS");
  const emailInput = form.getByLabel("Email or username", { exact: true }).filter({ visible: true });
  const passwordInput = form.getByLabel("Password", { exact: true }).filter({ visible: true });
  await until(async () => (await emailInput.count()) === 1 && (await passwordInput.count()) === 1, "CANARY_SIGN_IN_FIELDS_AMBIGUOUS");
  onPhase?.("canary_clone_sign_in_credentials");
  await emailInput.fill(email); await passwordInput.fill(password);
  onPhase?.("canary_clone_sign_in_submit");
  await form.getByRole("button", { name: "Sign in", exact: true }).click();
}

export async function restoreCanaryStar(page: Page, sql: Sql, userId: string, state: CanaryLifecycleState, origin: string, onPhase?: (phase: string) => void) {
  if (!state.starTouched || state.initialStarred === undefined) return;
  const current = await sql`SELECT 1 FROM followed_company WHERE user_id=${userId} AND company_id=${state.company.id}`;
  check(current.length <= 1, "CANARY_STAR_STATE_AMBIGUOUS");
  if (Boolean(current.length) !== state.initialStarred) {
    const path = `/en/company/${state.company.slug}`;
    const reused = await canaryRouteReusable(page, { origin, path, button: current.length ? "Starred" : "Star" });
    onPhase?.(reused ? "canary_star_restore_reuse" : "canary_star_restore_navigation");
    if (!reused) await navigateCanary(page, path);
    await page.getByRole("button", { name: "Account menu", exact: true }).waitFor();
    await page.getByRole("button", { name: current.length ? "Starred" : "Star", exact: true }).click();
    await until(async () => (await sql`SELECT 1 FROM followed_company WHERE user_id=${userId} AND company_id=${state.company.id}`).length === Number(state.initialStarred), "CANARY_STAR_RESTORE_FAILED");
  }
  state.starTouched = false;
}

/** Readiness diagnostics cannot certify a lifecycle with incomplete cleanup. */
export function requireCanaryCleanup(proof: { recovered: boolean; residual: number | null; starRestored: boolean; sessionClosed: boolean }) {
  check(proof.recovered && proof.residual === 0 && proof.starRestored && proof.sessionClosed, "CANARY_CLEANUP_INCOMPLETE");
}

/** One actual UI lifecycle shared by the local harness and protected staged gate. */
export async function exerciseCanaryLifecycle(input: {
  page: Page; sql: Sql; userId: string; email: string; password: string; origin: string; watchlistId: string;
  state: CanaryLifecycleState; onPhase?: (phase: string) => void;
  tailPacing?: CanaryTailPolicy;
  onEvidence?: (evidence: CanaryOwnerEvidence) => void;
  readReadinessState?: (page: Page, watchlistId: string, title: string, readiness: CanaryOwnerEvidence["readiness"]) => Promise<CanaryReadinessState>;
  createAnonymousContext: () => Promise<BrowserContext>;
}) {
  const { page, sql, userId, email, password, origin, watchlistId, state } = input;
  input.onPhase?.("canary_edit");
  const targetTitle = `${state.titles[0]}:edited`;
  state.titles.push(targetTitle); // Exact recovery namespace registered before the request.
  const [source] = await sql`SELECT user_id, title FROM watchlist WHERE id=${watchlistId}`;
  check(source?.user_id === userId && state.titles.includes(source.title), "CANARY_EDIT_OWNERSHIP_MISMATCH");
  await page.getByRole("heading", { level: 1 }).getByRole("button", { name: source.title, exact: true }).click();
  const titleInput = page.locator('input[maxlength="100"]');
  await titleInput.fill(targetTitle); await titleInput.press("Enter");
  await until(async () => (await sql`SELECT 1 FROM watchlist WHERE id=${watchlistId} AND user_id=${userId} AND title=${targetTitle}`).length === 1, "CANARY_EDIT_NOT_COMMITTED");
  input.onPhase?.("canary_share");
  await page.getByRole("button", { name: "Share", exact: true }).click();
  await until(async () => (await sql`SELECT 1 FROM watchlist WHERE id=${watchlistId} AND user_id=${userId} AND title=${targetTitle} AND share_enabled AND NOT alerts_enabled`).length === 1, "CANARY_SHARE_NOT_COMMITTED");

  input.onPhase?.("canary_anonymous_read");
  const anonymous = await input.createAnonymousContext();
  let shared: Page | undefined;
  try {
    shared = await anonymous.newPage(); shared.setDefaultTimeout(30_000);
    await navigateCanary(shared, `/en/watchlists/${watchlistId}`);
    await shared.getByRole("link", { name: state.company.name, exact: true }).waitFor();
    // This header appears only after the real session bootstrap effect settles.
    await shared.getByRole("link", { name: "Log in", exact: true }).waitFor();
    input.onPhase?.("canary_anonymous_clone");
    await shared.getByRole("button", { name: "Clone", exact: true }).click();
    await shared.waitForURL(url => /\/en\/watchlists\/[0-9a-f-]{36}$/.test(url.pathname) && !url.pathname.endsWith(watchlistId));
    // Browser-backed clone handoff, followed by a real dedicated-account sign-in.
    input.onPhase?.("canary_clone_sign_in");
    await navigateCanary(shared, `/en/sign-in?next=${encodeURIComponent("/en/watchlists")}`);
    await signInCanaryAccount(shared, email, password, input.onPhase);
    await shared.waitForURL(url => url.pathname.startsWith("/en/watchlists"));
    const session = await anonymous.request.get("/api/auth/get-session", { maxRedirects: 0 });
    check(session.status() === 200 && (await session.json()).user?.id === userId, "CANARY_CLONE_IDENTITY_MISMATCH");
    input.onPhase?.("canary_clone_handoff");
    // Sign-in already mounted the overview and started importing its pending clone.
    // A hard navigation here can interrupt removal of that intent and replay the copy.
    // Wait for the existing import and its real redirect instead.
    let cloneId: string | undefined;
    await until(async () => {
      const copies = await sql`SELECT id FROM watchlist WHERE user_id=${userId} AND title=${targetTitle} AND id<>${watchlistId}`;
      check(copies.length <= 1, "CANARY_CLONE_NAMESPACE_AMBIGUOUS");
      if (copies.length !== 1) return false;
      cloneId = copies[0].id; return true;
    }, "CANARY_CLONE_HANDOFF_NOT_COMMITTED");
    check(cloneId, "CANARY_CLONE_ID_MISSING");
    const copied = await sql`SELECT wc.company_id, w.alerts_enabled, w.share_enabled, w.filters->>'anyCompany' AS any_company
      FROM watchlist w JOIN watchlist_company wc ON wc.watchlist_id=w.id WHERE w.id=${cloneId} AND w.user_id=${userId} AND w.title=${targetTitle}`;
    check(copied.length === 1 && copied[0].company_id === state.company.id && !copied[0].alerts_enabled && !copied[0].share_enabled && copied[0].any_company !== "true", "CANARY_CLONE_SELECTION_MISMATCH");
    await shared.waitForURL(url => url.pathname === `/en/watchlists/${cloneId}`);
    input.onPhase?.("canary_clone_cleanup_owner_shell");
    await waitForCanaryOwnerShell(shared, targetTitle, { stage: "clone_cleanup", expectedPath: `/en/watchlists/${cloneId}`, expectedOrigin: origin, referenceCoverage: state.referenceCoverage, onPhase: input.onPhase, onEvidence: input.onEvidence, readState: input.readReadinessState ? readiness => input.readReadinessState!(shared!, cloneId!, targetTitle, readiness) : undefined });
    await shared.getByRole("button", { name: `Remove ${state.company.name}`, exact: true }).waitFor();
    input.onPhase?.("canary_clone_cleanup_trigger");
    await shared.getByRole("button", { name: "Delete", exact: true }).click();
    input.onPhase?.("canary_clone_cleanup_confirmation");
    await shared.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
    input.onPhase?.("canary_clone_cleanup_persistence");
    await shared.waitForURL(/\/en\/watchlists$/);
    check((await sql`SELECT 1 FROM watchlist WHERE id=${cloneId}`).length === 0, "CANARY_CLONE_CLEANUP_FAILED");
  } finally {
    try {
      const signedOut = await anonymous.request.post("/api/auth/sign-out", { data: {}, headers: { origin }, maxRedirects: 0 });
      check(signedOut.status() === 200, "CANARY_CLONE_SESSION_CLEANUP_FAILED");
    } finally { await anonymous.close(); }
  }

  // Both clone sign-out and context closure have completed. Remote tails must
  // start in a fresh burst window; an omitted policy safely retains remote pacing.
  input.onPhase?.("canary_tail_burst_settle");
  await settleCanaryTail(input.tailPacing ?? { target: "production" }, ms => page.waitForTimeout(ms));

  input.onPhase?.("canary_star");
  const initialStar = await sql`SELECT 1 FROM followed_company WHERE user_id=${userId} AND company_id=${state.company.id}`;
  check(initialStar.length <= 1, "CANARY_STAR_STATE_AMBIGUOUS"); state.initialStarred = Boolean(initialStar.length);
  const [policy] = await sql`SELECT notifications_paused FROM user_preferences WHERE user_id=${userId}`;
  check(policy?.notifications_paused === true, "CANARY_NOTIFICATIONS_NOT_PAUSED");
  await navigateCanary(page, `/en/company/${state.company.slug}`);
  await page.getByRole("button", { name: "Account menu", exact: true }).waitFor();
  state.starTouched = true;
  await page.getByRole("button", { name: state.initialStarred ? "Starred" : "Star", exact: true }).click();
  await until(async () => (await sql`SELECT 1 FROM followed_company WHERE user_id=${userId} AND company_id=${state.company.id}`).length === Number(!state.initialStarred), "CANARY_STAR_NOT_COMMITTED");
  await restoreCanaryStar(page, sql, userId, state, origin, input.onPhase);

  input.onPhase?.("canary_removal");
  await navigateCanary(page, `/en/watchlists/${watchlistId}`);
  await page.getByRole("button", { name: `Remove ${state.company.name}`, exact: true }).click();
  await until(async () => (await sql`SELECT 1 FROM watchlist_company WHERE watchlist_id=${watchlistId}`).length === 0, "CANARY_REMOVAL_NOT_COMMITTED");
  input.onPhase?.("canary_removal_reload_navigation");
  await navigateCanary(page, page.url());
  input.onPhase?.("canary_removal_reload_owner_shell");
  await waitForCanaryOwnerShell(page, targetTitle, { stage: "removal_reload", expectedPath: `/en/watchlists/${watchlistId}`, expectedOrigin: origin, referenceCoverage: state.referenceCoverage, onPhase: input.onPhase, onEvidence: input.onEvidence, readState: input.readReadinessState ? readiness => input.readReadinessState!(page, watchlistId, targetTitle, readiness) : undefined });
  input.onPhase?.("canary_removal_reload_absence");
  check((await page.getByRole("button", { name: `Remove ${state.company.name}`, exact: true }).count()) === 0, "CANARY_REMOVAL_RELOAD_FAILED");
  check((await sql`SELECT 1 FROM company_reference WHERE id=${state.company.id}`).length === 1, "CANARY_REMOVAL_DELETED_REFERENCE");
  return { edit: true, share: true, anonymousRead: true, cloneHandoff: true, starRestored: true, removal: true };
}
