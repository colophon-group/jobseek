import type { Page, Request } from "playwright";

function failed(code: string): never { throw Object.assign(new Error(code), { code }); }

/** The production proxy returns integer seconds. Refuse other formats and excessive delays. */
export function canaryRetryAfter(value: string | undefined): number {
  if (!value || !/^[1-9][0-9]*$/.test(value)) return failed("CANARY_NAVIGATION_RETRY_AFTER_INVALID");
  const seconds = Number(value);
  if (!Number.isSafeInteger(seconds) || seconds > 65) return failed("CANARY_NAVIGATION_RETRY_AFTER_EXCESSIVE");
  return seconds * 1000;
}

/** Only explicit document GET navigation; never retry a Server Action or form submission. */
export async function navigateCanary(page: Page, destination: string, sleep = (ms: number) => page.waitForTimeout(ms)) {
  const observation = observeCanaryReadiness(page);
  observation.requests = []; observation.errors = []; observation.truncated = false;
  const started = Date.now();
  for (let attempt = 0; attempt < 2; attempt++) {
    const response = await page.goto(destination);
    observation.navigation = { httpStatus: response?.status() ?? null, elapsedMs: boundedElapsed(started) };
    if (!response || response.request().method() !== "GET") return failed("CANARY_NAVIGATION_GET_NOT_PROVEN");
    if (response.status() === 429) {
      if (attempt !== 0) return failed("CANARY_NAVIGATION_RATE_LIMIT_EXHAUSTED");
      await sleep(canaryRetryAfter(response.headers()["retry-after"]));
      continue;
    }
    if (response.status() < 200 || response.status() >= 300) return failed("CANARY_NAVIGATION_HTTP_FAILED");
    return;
  }
}

export type CanaryReadinessState = {
  persistedTitleMatches: boolean | null;
  companyMembershipCount: number | null;
  anyCompany: boolean | null;
  sessionStatus: number | null;
  sessionIdentityMatches: boolean | null;
};
type Stage = "clone_cleanup" | "removal_reload" | "cleanup" | "readonly_reload";
type Coverage = "first_use" | "existing_reference" | "unspecified";
type Observation = {
  navigation: { httpStatus: number | null; elapsedMs: number | null };
  requests: { kind: "session_get" | "server_action_candidate"; status: "pending" | "response" | "transport_failure"; httpStatus: number | null; elapsedMs: number | null }[];
  errors: { category: string; nextDigest: string | null }[];
  truncated: boolean;
};
export type CanaryOwnerEvidence = {
  stage: Stage; referenceCoverage: Coverage; firstUse: boolean;
  readiness: "ready" | "header_not_ready" | "title_not_ready";
  route: "owned_watchlist" | "other" | "unavailable"; expectedRouteMatches: boolean | null; expectedOriginMatches: boolean | null;
  navigation: Observation["navigation"]; elapsedMs: number;
  visibility: Record<"account" | "accountDom" | "logIn" | "heading" | "editableTitle" | "expectedTitle" | "expectedTitleDom" | "loading" | "dialog" | "alertDialog", number | null>;
  state: CanaryReadinessState;
  requests: Observation["requests"]; browserErrors: Observation["errors"];
  evidenceTruncated: boolean;
};
export type CanaryOwnerOptions = {
  stage: Stage; expectedPath: string; expectedOrigin: string;
  referenceCoverage?: Coverage;
  onPhase?: (phase: string) => void;
  onEvidence?: (evidence: CanaryOwnerEvidence) => void;
  readState?: (readiness: CanaryOwnerEvidence["readiness"]) => Promise<CanaryReadinessState>;
};
const observations = new WeakMap<Page, Observation>();
const boundedElapsed = (started: number) => Math.max(0, Math.min(120_000, Date.now() - started));

/** Never inspect a browser error message/stack; Next digests must be numeric. */
export function canaryBrowserError(error: unknown): Observation["errors"][number] {
  try {
    const value = error as { name?: unknown; digest?: unknown };
    const categories = new Map([["TypeError", "type_error"], ["ReferenceError", "reference_error"], ["SyntaxError", "syntax_error"], ["RangeError", "range_error"], ["Error", "error"]]);
    return { category: typeof value?.name === "string" ? categories.get(value.name) ?? "unknown" : "unknown",
      nextDigest: typeof value?.digest === "string" && /^[0-9]{1,16}$/.test(value.digest) ? value.digest : null };
  } catch { return { category: "unknown", nextDigest: null }; }
}

/** Passive metadata only. An opaque Server Action cannot be identified as bootstrap. */
export function observeCanaryReadiness(page: Page): Observation {
  const existing = observations.get(page); if (existing) return existing;
  const observed: Observation = { navigation: { httpStatus: null, elapsedMs: null }, requests: [], errors: [], truncated: false };
  observations.set(page, observed);
  const starts = new WeakMap<Request, { row: Observation["requests"][number]; started: number }>();
  page.on("request", request => {
    try {
      const url = new URL(request.url());
      if (url.origin !== new URL(page.url()).origin) return;
      const kind = request.method() === "GET" && url.pathname === "/api/auth/get-session" ? "session_get"
        : request.method() === "POST" && request.headers()["next-action"] ? "server_action_candidate" : null;
      if (kind) {
        if (observed.requests.length >= 8) { observed.truncated = true; return; }
        const row: Observation["requests"][number] = { kind, status: "pending", httpStatus: null, elapsedMs: null };
        observed.requests.push(row); starts.set(request, { row, started: Date.now() });
      }
    } catch { /* No untrusted request details enter diagnostics. */ }
  });
  page.on("response", response => {
    const start = starts.get(response.request()); if (!start) return;
    Object.assign(start.row, { status: "response", httpStatus: response.status(), elapsedMs: boundedElapsed(start.started) });
  });
  page.on("requestfailed", request => {
    const start = starts.get(request); if (start) Object.assign(start.row, { status: "transport_failure", elapsedMs: boundedElapsed(start.started) });
  });
  page.on("pageerror", error => {
    if (observed.errors.length >= 8) { observed.truncated = true; return; }
    observed.errors.push(canaryBrowserError(error));
  });
  return observed;
}

const unknownState: CanaryReadinessState = { persistedTitleMatches: null, companyMembershipCount: null, anyCompany: null, sessionStatus: null, sessionIdentityMatches: null };
async function ownerEvidence(page: Page, title: string, options: CanaryOwnerOptions, readiness: CanaryOwnerEvidence["readiness"], started: number): Promise<CanaryOwnerEvidence> {
  const observation = observeCanaryReadiness(page);
  const account = page.getByRole("button", { name: "Account menu", exact: true });
  const heading = page.getByRole("heading", { level: 1 });
  const locators = { account, accountDom: page.getByRole("button", { name: "Account menu", exact: true, includeHidden: true }),
    logIn: page.getByRole("link", { name: "Log in", exact: true }), heading,
    editableTitle: heading.getByRole("button"), expectedTitle: heading.getByRole("button", { name: title, exact: true }),
    expectedTitleDom: page.getByRole("heading", { level: 1, includeHidden: true }).getByRole("button", { name: title, exact: true, includeHidden: true }),
    loading: page.getByRole("status", { name: "Loading watchlist", exact: true }), dialog: page.getByRole("dialog"), alertDialog: page.getByRole("alertdialog") };
  const visibility = {} as CanaryOwnerEvidence["visibility"];
  let visibilityTimer: ReturnType<typeof setTimeout> | undefined;
  for (const key of Object.keys(locators)) visibility[key as keyof typeof visibility] = null;
  try {
    await Promise.race([Promise.all(Object.entries(locators).map(async ([key, locator]) => {
      visibility[key as keyof typeof visibility] = await locator.filter({ visible: true }).count().then(count => Math.min(9, count)).catch(() => null);
    })), new Promise<void>(resolve => { visibilityTimer = setTimeout(resolve, 500); })]);
  } finally { clearTimeout(visibilityTimer); }
  let state = { ...unknownState }; let timer: ReturnType<typeof setTimeout> | undefined;
  if (options.readState) {
    try {
      const value = await Promise.race([options.readState(readiness), new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("diagnostic_budget")), 1_500); })]);
      state = { persistedTitleMatches: typeof value.persistedTitleMatches === "boolean" ? value.persistedTitleMatches : null,
        companyMembershipCount: Number.isSafeInteger(value.companyMembershipCount) && value.companyMembershipCount! >= 0 && value.companyMembershipCount! <= 1_000 ? value.companyMembershipCount : null,
        anyCompany: typeof value.anyCompany === "boolean" ? value.anyCompany : null,
        sessionStatus: Number.isInteger(value.sessionStatus) && value.sessionStatus! >= 100 && value.sessionStatus! <= 599 ? value.sessionStatus : null,
        sessionIdentityMatches: typeof value.sessionIdentityMatches === "boolean" ? value.sessionIdentityMatches : null };
    } catch { /* Missing diagnostics must never replace the actual readiness failure. */ }
    finally { clearTimeout(timer); }
  }
  let route: CanaryOwnerEvidence["route"] = "unavailable"; let expectedRouteMatches: boolean | null = null; let expectedOriginMatches: boolean | null = null;
  try { const url = new URL(page.url()); expectedOriginMatches = url.origin === options.expectedOrigin; const path = url.pathname; route = /^\/en\/watchlists\/[0-9a-f-]{36}$/.test(path) ? "owned_watchlist" : "other"; expectedRouteMatches = path === options.expectedPath; } catch { /* No URL is emitted. */ }
  return { stage: options.stage, referenceCoverage: options.referenceCoverage ?? "unspecified", firstUse: false, readiness,
    route, expectedRouteMatches, expectedOriginMatches, navigation: { ...observation.navigation }, elapsedMs: boundedElapsed(started), visibility: { ...visibility }, state,
    requests: observation.requests.map(value => ({ ...value })), browserErrors: observation.errors.map(value => ({ ...value })), evidenceTruncated: observation.truncated };
}

/** The original two locator waits retain their exact default budgets and assertions. */
export async function waitForCanaryOwnerShell(page: Page, title: string, options?: CanaryOwnerOptions) {
  observeCanaryReadiness(page); const started = Date.now(); let readiness: CanaryOwnerEvidence["readiness"] = "header_not_ready";
  try {
    options?.onPhase?.(`canary_${options.stage}_owner_header`);
    await page.getByRole("button", { name: "Account menu", exact: true }).filter({ visible: true }).waitFor();
    readiness = "title_not_ready";
    options?.onPhase?.(`canary_${options.stage}_owner_title`);
    await page.getByRole("heading", { level: 1 }).getByRole("button", { name: title, exact: true }).filter({ visible: true }).waitFor();
    readiness = "ready";
  } finally {
    if (options?.onEvidence) {
      // Diagnostics are supplemental. Neither a probe failure nor a consumer
      // exception can turn a failed original wait into success or bypass cleanup.
      try { options.onEvidence(await ownerEvidence(page, title, options, readiness, started)); } catch { /* Preserve original wait outcome. */ }
    }
  }
}

/** Operator-owned fixture GET reload only; never qualifies as promotion first-use. */
export async function diagnoseCanaryOwnerReload(page: Page, title: string, options: Omit<CanaryOwnerOptions, "stage" | "referenceCoverage">) {
  if (!/^\/en\/watchlists\/[0-9a-f-]{36}$/.test(options.expectedPath)) return failed("CANARY_DIAGNOSTIC_ROUTE_INVALID");
  await navigateCanary(page, new URL(options.expectedPath, options.expectedOrigin).href);
  if (new URL(page.url()).origin !== options.expectedOrigin) return failed("CANARY_DIAGNOSTIC_ORIGIN_MISMATCH");
  await waitForCanaryOwnerShell(page, title, { ...options, stage: "readonly_reload", referenceCoverage: "existing_reference" });
  return { firstUse: false, referenceCoverage: "existing_reference" as const, readOnlyReload: true };
}
