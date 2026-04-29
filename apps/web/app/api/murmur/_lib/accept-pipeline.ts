/**
 * Orchestration helpers for `POST /api/murmur/accept`.
 *
 * The route is laid out as a small pipeline of pure-ish steps so each
 * step can be exercised in isolation:
 *
 *   1. `parseAndCapBody`     — read the body honouring the 5 MB cap.
 *   2. `validateAcceptBody`  — dialect-aware schema check.
 *   3. `classifyIdempotency` — ledger lookup; returns fresh / already /
 *                               body_mismatch.
 *   4. `rerunProbes`         — defense-in-depth re-run of probe/run
 *                               via J5's `invokeLib`. Wrapped in a 30s
 *                               wallclock budget.
 *   5. `applyCatalog`        — write to Postgres or CSV.
 *
 * The route handler glues these together and translates outcomes into
 * the M0 envelope. This file lays out the contracts only — no bodies.
 *
 * @see colophon-group/jobseek#2763
 */

import type { FinalOutput } from "./accept-schema";

/** Outcome of `parseAndCapBody`. */
export type ParseBodyResult =
  | { readonly status: "ok"; readonly raw: string; readonly body: unknown }
  | { readonly status: "too_large" }
  | { readonly status: "invalid_json" };

/** Maximum webhook body size, in bytes. Murmur DESIGN.md §4.1: 5 MB. */
export const MAX_BODY_BYTES = 5 * 1024 * 1024;

/**
 * Read the body off the Request, refusing if `Content-Length` declares
 * more than 5 MB and again if the post-read buffer exceeds the cap
 * (defends against missing / lying `Content-Length`). Parses the
 * resulting buffer as JSON; returns `invalid_json` on any parse error.
 *
 * Tests pass `Request` objects directly. Production routes pass the
 * incoming Next.js `Request`.
 */
export function parseAndCapBody(_request: Request): Promise<ParseBodyResult> {
  throw new Error("not implemented");
}

/** Outcome of the probe re-run. */
export type RerunProbesResult =
  | { readonly status: "ok" }
  | { readonly status: "failed"; readonly errors: readonly string[] }
  | { readonly status: "timeout" };

/**
 * Re-run probe + run for every board in the validated `final_output`.
 *
 * Implementation strategy:
 *   - For each board, call `invokeLib("probe_monitor", { board_url })`
 *     and (when `monitor_type` is non-`"skip"`) `invokeLib("run_monitor",
 *     { board_url })`. The same for the scraper.
 *   - All board calls run in parallel; the function resolves when the
 *     last one finishes OR when the overall 30s wallclock timer fires,
 *     whichever comes first.
 *   - Any per-board lib failure aggregates into the returned
 *     `errors: [...]` list with the format `probe_failed:<board_alias>`
 *     so the operator can identify which board went bad.
 *   - On wallclock-timeout the function returns `{ status: "timeout" }`;
 *     the route maps this to HTTP 504 with `errors: ["probe_timeout"]`.
 *
 * The 30s budget is governed by `MURMUR_ACCEPT_PROBE_TIMEOUT_MS`
 * (defaults to 30000) so tests can shrink it.
 *
 * Tests stub this function via `RerunHolder.current` (mirrors the
 * `InvokerHolder` pattern from J5).
 */
export type RerunProbes = (body: FinalOutput) => Promise<RerunProbesResult>;

/** Default implementation — wraps `invokeLib` with the 30s budget. */
export const defaultRerunProbes: RerunProbes = async () => {
  throw new Error("not implemented");
};

/** Mutable holder for tests to override. */
export const RerunHolder: { current: RerunProbes } = {
  current: defaultRerunProbes,
};

/** Convenience pass-through used by the route. */
export function rerunProbes(body: FinalOutput): Promise<RerunProbesResult> {
  return RerunHolder.current(body);
}
