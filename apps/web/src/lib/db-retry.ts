/**
 * Postgres connection-class retry helper.
 *
 * Background — issue #2918: a Vercel production build at
 * 2026-05-09T15:41:49Z failed during OG-image prerender for
 * `/en/company/chevron/opengraph-image-y6bp13`. The next build two
 * minutes later succeeded. The failing query was the company-by-slug
 * Postgres fallback (`_fetchCompanyBySlugFromPostgres`), and the cause
 * was `read ECONNRESET` against the Supabase pooler — a transient
 * connection event, not a structural break.
 *
 * Project memory rule: "Supabase is fragile — light queries only ...
 * use local Postgres for company/posting counts." This helper does NOT
 * violate that rule; it makes existing queries more resilient to known
 * transient fragility (pooler restarts, idle-connection drops) by
 * retrying connection-class errors with exponential backoff + jitter.
 *
 * Retry policy:
 *   - 3 attempts total (initial + 2 retries)
 *   - Exponential backoff: 200ms / 400ms / 800ms baseline + 0..100ms jitter
 *   - Retry only on transient connection errors (see `isRetryable`)
 *   - Non-retryable errors (syntax, constraint, business logic) propagate
 *     immediately so callers see the real failure
 *   - `console.warn` on every retry so observability picks it up
 *
 * Scope of this PR: wraps `_fetchCompanyBySlugFromPostgres` only. Other
 * build-critical Postgres call sites (sitemap, related-posts, company
 * directory) are likely to want the same protection — flagged as a
 * follow-up rather than expanded mechanically here.
 */

const RETRYABLE_ERROR_CODES = new Set([
  "ECONNRESET",
  "ETIMEDOUT",
  "ECONNREFUSED",
  "EPIPE",
]);

/**
 * Substring matches against `err.message` for transient pooler/server
 * events that don't surface a Node `code`. These come from postgres.js
 * and the Supabase pgbouncer:
 *   - "Connection terminated"           — postgres.js socket closed
 *   - "terminating connection due to administrator command" — pooler restart
 *   - "Connection terminated unexpectedly" — postgres.js variant
 *   - "Connection ended"                — postgres.js graceful close
 */
const RETRYABLE_MESSAGE_FRAGMENTS = [
  "connection terminated",
  "terminating connection",
  "connection ended",
  "connection closed",
];

export function isRetryableError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const e = err as { code?: unknown; message?: unknown; cause?: unknown };
  if (typeof e.code === "string" && RETRYABLE_ERROR_CODES.has(e.code)) {
    return true;
  }
  if (typeof e.message === "string") {
    const lower = e.message.toLowerCase();
    for (const frag of RETRYABLE_MESSAGE_FRAGMENTS) {
      if (lower.includes(frag)) return true;
    }
  }
  // postgres.js wraps the network error in a `Failed query: ... [cause]: …`
  // shape — recurse into `cause` once so we still catch the inner ECONNRESET.
  if (e.cause !== undefined && e.cause !== err) {
    return isRetryableError(e.cause);
  }
  return false;
}

export interface DbRetryOptions {
  /** Total attempts (initial + retries). Defaults to 3. */
  attempts?: number;
  /**
   * Base delays in ms before each retry attempt. The N-th retry waits
   * `baseDelaysMs[N-1] + jitter`. Defaults to [200, 400, 800].
   */
  baseDelaysMs?: number[];
  /** Max additional jitter added to each delay. Defaults to 100ms. */
  maxJitterMs?: number;
  /** Sleep override for tests. */
  sleep?: (ms: number) => Promise<void>;
  /** Predicate override for tests / niche call-sites. */
  isRetryable?: (err: unknown) => boolean;
  /** Label used in retry log lines. */
  label?: string;
}

const defaultSleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Run `fn`, retrying on transient connection-class errors. Returns the
 * first successful result, or throws the last error after exhausting
 * the attempt budget. The final throw preserves the original exception
 * (no wrapping) so call-site error handling stays unchanged.
 */
export async function withDbRetry<T>(
  fn: () => Promise<T>,
  opts: DbRetryOptions = {},
): Promise<T> {
  const attempts = opts.attempts ?? 3;
  const baseDelays = opts.baseDelaysMs ?? [200, 400, 800];
  const maxJitter = opts.maxJitterMs ?? 100;
  const sleep = opts.sleep ?? defaultSleep;
  const retryable = opts.isRetryable ?? isRetryableError;
  const label = opts.label ?? "db";

  let lastErr: unknown;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastErr = err;
      const isLast = attempt >= attempts;
      if (isLast || !retryable(err)) {
        throw err;
      }
      const baseDelay = baseDelays[attempt - 1] ?? baseDelays[baseDelays.length - 1] ?? 200;
      const jitter = Math.floor(Math.random() * (maxJitter + 1));
      const delay = baseDelay + jitter;
      const code = (err as { code?: unknown }).code;
      const message = (err as { message?: unknown }).message;
      console.warn(
        `[${label}] transient error on attempt ${attempt}/${attempts}, ` +
          `retrying in ${delay}ms ` +
          `(code=${typeof code === "string" ? code : "n/a"}, ` +
          `message=${typeof message === "string" ? message.slice(0, 200) : "n/a"})`,
      );
      await sleep(delay);
    }
  }
  // Unreachable: the loop either returns or throws. Re-throw lastErr to
  // satisfy the type checker.
  throw lastErr;
}
