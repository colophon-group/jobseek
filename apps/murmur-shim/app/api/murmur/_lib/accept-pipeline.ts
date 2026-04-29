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
 * the M0 envelope.
 *
 * @see colophon-group/jobseek#2763
 */

import type { FinalOutput } from "./accept-schema";
import { invokeLib } from "./invoke-lib";

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
 */
export async function parseAndCapBody(
  request: Request,
): Promise<ParseBodyResult> {
  const declared = request.headers.get("content-length");
  if (declared !== null) {
    const n = Number(declared);
    if (Number.isFinite(n) && n > MAX_BODY_BYTES) {
      return { status: "too_large" };
    }
  }
  // Read the buffer ourselves so we can enforce the cap regardless of
  // what Content-Length claimed (or didn't claim).
  let raw: string;
  try {
    raw = await request.text();
  } catch {
    return { status: "invalid_json" };
  }
  if (Buffer.byteLength(raw, "utf8") > MAX_BODY_BYTES) {
    return { status: "too_large" };
  }
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return { status: "invalid_json" };
  }
  return { status: "ok", raw, body };
}

/** Outcome of the probe re-run. */
export type RerunProbesResult =
  | { readonly status: "ok" }
  | { readonly status: "failed"; readonly errors: readonly string[] }
  | { readonly status: "timeout" };

/**
 * Re-run probe + run for every board in the validated `final_output`.
 *
 * Strategy:
 *   - For each board, call `invokeLib("probe_monitor", { board_url })`
 *     in parallel. We rely on the shim's typed-error envelope to
 *     surface failures.
 *   - All board calls run in parallel; a wallclock timer races against
 *     the aggregate.
 *   - Per-board lib failure aggregates into `errors: ["<token>:<alias>"]`.
 *
 * The 30s budget is governed by `MURMUR_ACCEPT_PROBE_TIMEOUT_MS`
 * (defaults to 30000) so tests can shrink it.
 */
export type RerunProbes = (body: FinalOutput) => Promise<RerunProbesResult>;

const ACCEPT_TIMEOUT_ENV = "MURMUR_ACCEPT_PROBE_TIMEOUT_MS";
const DEFAULT_TIMEOUT_MS = 30000;

/**
 * Parse a positive-integer milliseconds value from env. Falls back to
 * the default on missing / non-numeric / non-positive input. Logs a
 * warning so misconfiguration is visible without crashing the route.
 */
function resolveTimeoutMs(): number {
  const raw = process.env[ACCEPT_TIMEOUT_ENV];
  if (raw === undefined || raw.trim() === "") return DEFAULT_TIMEOUT_MS;
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) {
    console.warn(
      `[murmur accept] ignoring invalid ${ACCEPT_TIMEOUT_ENV}=${raw}; using default ${DEFAULT_TIMEOUT_MS}ms`,
    );
    return DEFAULT_TIMEOUT_MS;
  }
  return n;
}

export const defaultRerunProbes: RerunProbes = async (body) => {
  const timeoutMs = resolveTimeoutMs();

  const racing = (async () => {
    const probeResults = await Promise.all(
      body.boards.map(async (board) => {
        const result = await invokeLib(
          "probe_monitor",
          { board_url: board.board_url },
          /* claim_token */ "",
        );
        return { board, result };
      }),
    );
    const errors: string[] = [];
    for (const { board, result } of probeResults) {
      if (!result.ok) {
        const tokens = result.errors ?? ["probe_failed"];
        for (const t of tokens) {
          errors.push(`${t}:${board.alias}`);
        }
      }
    }
    return errors;
  })();

  let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<"timeout">((resolve) => {
    timeoutHandle = setTimeout(() => resolve("timeout"), timeoutMs);
  });

  try {
    const winner = await Promise.race([racing, timeout]);
    if (winner === "timeout") {
      return { status: "timeout" };
    }
    if ((winner as string[]).length > 0) {
      return { status: "failed", errors: winner as string[] };
    }
    return { status: "ok" };
  } finally {
    if (timeoutHandle !== undefined) clearTimeout(timeoutHandle);
  }
};

/** Mutable holder for tests to override. */
export const RerunHolder: { current: RerunProbes } = {
  current: defaultRerunProbes,
};

/** Convenience pass-through used by the route. */
export function rerunProbes(body: FinalOutput): Promise<RerunProbesResult> {
  return RerunHolder.current(body);
}
