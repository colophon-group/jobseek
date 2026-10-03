import type { BrowserContext, Page } from "playwright";
import type { Sql } from "postgres";

export type CanaryLifecycleState = {
  titles: string[];
  company: { id: string; name: string; slug: string };
  initialStarred?: boolean;
  starTouched: boolean;
};
function check(condition: unknown, code: string): asserts condition {
  if (!condition) throw Object.assign(new Error(code), { code });
}
async function until(probe: () => Promise<boolean>, code: string) {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) { if (await probe()) return; await new Promise(resolve => setTimeout(resolve, 100)); }
  throw Object.assign(new Error(code), { code });
}

export async function restoreCanaryStar(page: Page, sql: Sql, userId: string, state: CanaryLifecycleState) {
  if (!state.starTouched || state.initialStarred === undefined) return;
  const current = await sql`SELECT 1 FROM followed_company WHERE user_id=${userId} AND company_id=${state.company.id}`;
  check(current.length <= 1, "CANARY_STAR_STATE_AMBIGUOUS");
  if (Boolean(current.length) !== state.initialStarred) {
    await page.goto(`/en/company/${state.company.slug}`);
    await page.getByRole("button", { name: "Account menu", exact: true }).waitFor();
    await page.getByRole("button", { name: current.length ? "Starred" : "Star", exact: true }).click();
    await until(async () => (await sql`SELECT 1 FROM followed_company WHERE user_id=${userId} AND company_id=${state.company.id}`).length === Number(state.initialStarred), "CANARY_STAR_RESTORE_FAILED");
  }
  state.starTouched = false;
}

/** One actual UI lifecycle shared by the local harness and protected staged gate. */
export async function exerciseCanaryLifecycle(input: {
  page: Page; sql: Sql; userId: string; email: string; password: string; origin: string; watchlistId: string;
  state: CanaryLifecycleState; onPhase?: (phase: string) => void; createAnonymousContext: () => Promise<BrowserContext>;
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
    await shared.goto(`/en/watchlists/${watchlistId}`);
    await shared.getByRole("link", { name: state.company.name, exact: true }).waitFor();
    // This header appears only after the real session bootstrap effect settles.
    await shared.getByRole("link", { name: "Log in", exact: true }).waitFor();
    input.onPhase?.("canary_anonymous_clone");
    await shared.getByRole("button", { name: "Clone", exact: true }).click();
    await shared.waitForURL(url => /\/en\/watchlists\/[0-9a-f-]{36}$/.test(url.pathname) && !url.pathname.endsWith(watchlistId));
    // Browser-backed clone handoff, followed by a real dedicated-account sign-in.
    input.onPhase?.("canary_clone_sign_in");
    await shared.goto(`/en/sign-in?next=${encodeURIComponent("/en/watchlists")}`);
    await shared.getByLabel("Email or username", { exact: true }).fill(email);
    await shared.getByLabel("Password", { exact: true }).fill(password);
    await shared.getByRole("button", { name: "Sign in", exact: true }).click();
    await shared.waitForURL(url => url.pathname.startsWith("/en/watchlists"));
    const session = await anonymous.request.get("/api/auth/get-session", { maxRedirects: 0 });
    check(session.status() === 200 && (await session.json()).user?.id === userId, "CANARY_CLONE_IDENTITY_MISMATCH");
    input.onPhase?.("canary_clone_handoff");
    await shared.goto("/en/watchlists");
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
    await shared.getByRole("button", { name: `Remove ${state.company.name}`, exact: true }).waitFor();
    await shared.getByRole("button", { name: "Delete", exact: true }).click();
    await shared.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
    await shared.waitForURL(/\/en\/watchlists$/);
    check((await sql`SELECT 1 FROM watchlist WHERE id=${cloneId}`).length === 0, "CANARY_CLONE_CLEANUP_FAILED");
  } finally {
    try {
      const signedOut = await anonymous.request.post("/api/auth/sign-out", { data: {}, headers: { origin }, maxRedirects: 0 });
      check(signedOut.status() === 200, "CANARY_CLONE_SESSION_CLEANUP_FAILED");
    } finally { await anonymous.close(); }
  }

  input.onPhase?.("canary_star");
  const initialStar = await sql`SELECT 1 FROM followed_company WHERE user_id=${userId} AND company_id=${state.company.id}`;
  check(initialStar.length <= 1, "CANARY_STAR_STATE_AMBIGUOUS"); state.initialStarred = Boolean(initialStar.length);
  const [policy] = await sql`SELECT notifications_paused FROM user_preferences WHERE user_id=${userId}`;
  check(policy?.notifications_paused === true, "CANARY_NOTIFICATIONS_NOT_PAUSED");
  await page.goto(`/en/company/${state.company.slug}`);
  await page.getByRole("button", { name: "Account menu", exact: true }).waitFor();
  state.starTouched = true;
  await page.getByRole("button", { name: state.initialStarred ? "Starred" : "Star", exact: true }).click();
  await until(async () => (await sql`SELECT 1 FROM followed_company WHERE user_id=${userId} AND company_id=${state.company.id}`).length === Number(!state.initialStarred), "CANARY_STAR_NOT_COMMITTED");
  await restoreCanaryStar(page, sql, userId, state);

  input.onPhase?.("canary_removal");
  await page.goto(`/en/watchlists/${watchlistId}`);
  await page.getByRole("button", { name: `Remove ${state.company.name}`, exact: true }).click();
  await until(async () => (await sql`SELECT 1 FROM watchlist_company WHERE watchlist_id=${watchlistId}`).length === 0, "CANARY_REMOVAL_NOT_COMMITTED");
  await page.reload();
  check((await page.getByRole("button", { name: `Remove ${state.company.name}`, exact: true }).count()) === 0, "CANARY_REMOVAL_RELOAD_FAILED");
  check((await sql`SELECT 1 FROM company_reference WHERE id=${state.company.id}`).length === 1, "CANARY_REMOVAL_DELETED_REFERENCE");
  return { edit: true, share: true, anonymousRead: true, cloneHandoff: true, starRestored: true, removal: true };
}
