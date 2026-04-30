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
import { useEffect, useRef, useState } from "react";

/**
 * Polling cadence constants. Exported so tests can reference them without
 * hard-coding magic numbers.
 */
export const INITIAL_INTERVAL_MS = 5_000;
export const BACKOFF_AFTER_MS = 5 * 60 * 1000;
export const BACKOFF_INTERVAL_MS = 30_000;
export const GIVE_UP_AFTER_MS = 30 * 60 * 1000;

/**
 * Possible top-level states the hook returns to consumers.
 */
export type MurmurRunStatusState =
  | "idle"
  | "running"
  | "completed"
  | "given_up";

/**
 * Hook return shape.
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
 */
export interface UseMurmurRunStatusOptions {
  readonly fetchImpl?: typeof fetch;
  readonly now?: () => number;
  readonly getEndpoint?: (runId: string) => string;
}

/**
 * Default URL builder. The proxy lives at the same origin as the page, so a
 * relative URL is enough — the browser will attach session cookies.
 */
function defaultEndpoint(runId: string): string {
  return `/api/web/companies/request/${encodeURIComponent(runId)}/status`;
}

interface ProxyEnvelope {
  ok: boolean;
  data?: {
    status?: unknown;
    webhook_status?: unknown;
    slug?: unknown;
    company_id?: unknown;
  };
}

/** True if `value` is a non-empty string. */
function isStr(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/**
 * The hook. Pass `null` or an empty `runId` to disable polling — the hook
 * stays in `state: "idle"`.
 */
export function useMurmurRunStatus(
  runId: string | null,
  options?: UseMurmurRunStatusOptions,
): UseMurmurRunStatusResult {
  const [result, setResult] = useState<UseMurmurRunStatusResult>({
    state: runId ? "running" : "idle",
  });

  // Latest options ref so we don't have to retrigger the effect when callers
  // pass inline objects. The hook is keyed only on `runId`.
  const optsRef = useRef<UseMurmurRunStatusOptions | undefined>(options);
  optsRef.current = options;

  useEffect(() => {
    if (!runId) {
      setResult({ state: "idle" });
      return;
    }

    setResult({ state: "running" });

    const startedAt = (optsRef.current?.now ?? Date.now)();
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    async function tick(): Promise<void> {
      if (cancelled) return;
      const nowFn = optsRef.current?.now ?? Date.now;
      const fetchImpl = optsRef.current?.fetchImpl ?? fetch;
      const getEndpoint = optsRef.current?.getEndpoint ?? defaultEndpoint;

      const elapsed = nowFn() - startedAt;
      if (elapsed >= GIVE_UP_AFTER_MS) {
        if (!cancelled) {
          setResult((prev) => ({ ...prev, state: "given_up" }));
        }
        return;
      }

      try {
        const res = await fetchImpl(getEndpoint(runId as string), {
          method: "GET",
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (cancelled) return;
        if (res.ok) {
          let body: ProxyEnvelope | undefined;
          try {
            body = (await res.json()) as ProxyEnvelope;
          } catch {
            body = undefined;
          }
          const data = body?.data;
          if (data && isStr(data.status) && isStr(data.webhook_status)) {
            const slug = isStr(data.slug) ? data.slug : undefined;
            const companyId = isStr(data.company_id) ? data.company_id : undefined;
            const reachedDelivered = data.webhook_status === "delivered";
            if (!cancelled) {
              setResult({
                state: reachedDelivered ? "completed" : "running",
                status: data.status,
                webhookStatus: data.webhook_status,
                slug,
                companyId,
              });
            }
            if (reachedDelivered) {
              return;
            }
          }
        }
        // Non-2xx or malformed body: keep polling — surface as "running" with
        // whatever fields we already had. We deliberately don't expose error
        // states to the UI for the demo; a future iteration can add backoff.
      } catch (err) {
        if (
          (err as { name?: string } | null)?.name === "AbortError" ||
          controller.signal.aborted
        ) {
          return;
        }
        // network error — fall through to the next scheduled tick.
      }

      if (cancelled) return;
      const elapsedAfter =
        (optsRef.current?.now ?? Date.now)() - startedAt;
      if (elapsedAfter >= GIVE_UP_AFTER_MS) {
        if (!cancelled) {
          setResult((prev) => ({ ...prev, state: "given_up" }));
        }
        return;
      }
      const nextDelay =
        elapsedAfter >= BACKOFF_AFTER_MS
          ? BACKOFF_INTERVAL_MS
          : INITIAL_INTERVAL_MS;
      timer = setTimeout(tick, nextDelay);
    }

    void tick();

    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== null) clearTimeout(timer);
    };
  }, [runId]);

  return result;
}
