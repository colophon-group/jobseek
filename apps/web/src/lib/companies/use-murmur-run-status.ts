"use client";

/**
 * useMurmurRunStatus
 *
 * Client hook that polls `GET /api/web/companies/request/{run_id}/status`
 * and exposes a small state machine to the UI:
 *
 *   - `state: "idle"`       — `runId` is null/empty (do nothing).
 *   - `state: "running"`    — polling, latest server snapshot in `status`/`webhookStatus`.
 *   - `state: "completed"`  — `webhookStatus === "delivered"` was observed; polling stopped.
 *   - `state: "given_up"`   — exceeded {@link GIVE_UP_AFTER_MS}; polling stopped.
 *
 * Polling cadence:
 *   - First {@link BACKOFF_AFTER_MS} (5 minutes) at {@link INITIAL_INTERVAL_MS} (5s).
 *   - After 5 minutes, back off to {@link BACKOFF_INTERVAL_MS} (30s).
 *   - Stop after {@link GIVE_UP_AFTER_MS} (30 minutes).
 *
 * Implementation notes:
 *   - We use `setTimeout` recursion (not `setInterval`) so backoff is a
 *     single branch on each tick — and so unmount cancels cleanly.
 *   - Every fetch is guarded by an `AbortController` shared with the timer.
 *     On unmount we `abort()` and clear the timer so no late response can
 *     race a second mount.
 *   - The hook NEVER touches `MURMUR_TOKEN` directly. It calls the
 *     same-origin proxy, which holds the token server-side. Any client-bundle
 *     grep for `MURMUR_TOKEN` should miss this file by design.
 *
 * @see colophon-group/jobseek#2810
 */

/**
 * Polling cadence constants. Exported so tests can reference them without
 * hard-coding magic numbers.
 */
export const INITIAL_INTERVAL_MS = 5_000;
export const BACKOFF_AFTER_MS = 5 * 60 * 1000;
export const BACKOFF_INTERVAL_MS = 30_000;
export const GIVE_UP_AFTER_MS = 30 * 60 * 1000;

/**
 * Possible top-level states the hook returns to consumers. See the file
 * docstring for semantics.
 */
export type MurmurRunStatusState =
  | "idle"
  | "running"
  | "completed"
  | "given_up";

/**
 * Hook return shape. The `status`, `webhookStatus`, `slug`, and `companyId`
 * fields are populated as soon as we have a server snapshot; they're undefined
 * before the first response.
 */
export interface UseMurmurRunStatusResult {
  readonly state: MurmurRunStatusState;
  readonly status?: string;
  readonly webhookStatus?: string;
  readonly slug?: string;
  readonly companyId?: string;
}

/**
 * Test/override hooks. In production, callers pass nothing.
 *
 *   - `fetchImpl` lets tests stub the network without touching `globalThis.fetch`.
 *   - `now` lets tests control the deadline check (we use this instead of
 *     `Date.now` so fake-timer tests are deterministic).
 *   - `getEndpoint` lets tests rewrite the URL — the default builds it from
 *     `runId` against the proxy path.
 */
export interface UseMurmurRunStatusOptions {
  readonly fetchImpl?: typeof fetch;
  readonly now?: () => number;
  readonly getEndpoint?: (runId: string) => string;
}

/**
 * The hook. Pass `null` or an empty `runId` to disable polling — the hook
 * stays in `state: "idle"`.
 *
 * Re-renders the host component on every state transition (state change,
 * status update, or `webhookStatus` flip). Guarantees:
 *   - At most one in-flight request at a time per mount.
 *   - On unmount, any pending fetch is aborted and the next-tick timer is
 *     cleared.
 *   - The hook NEVER throws. Server errors keep the state at `"running"`
 *     and we retry on the next interval.
 */
export function useMurmurRunStatus(
  runId: string | null,
  options?: UseMurmurRunStatusOptions,
): UseMurmurRunStatusResult {
  throw new Error("not implemented");
}
