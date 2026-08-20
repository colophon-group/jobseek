/**
 * Typesense connection-class retry helper.
 *
 * Background — issue #3008: cold-start `/en/explore` visits showed
 * `Fetch failed loading: POST "https://jseek.co/en/explore"` for some
 * server actions. A Playwright reproduction against production caught
 * 3/5 trials with `net::ERR_ABORTED` on `POST /en/explore` server-action
 * calls. The aborts correlate with cold serverless instances opening
 * the first connection to `typesense.colophon-group.org` over the
 * Cloudflare tunnel — a single TLS handshake + first query on a cold
 * Lambda + cold Typesense connection can exceed the 5s
 * `connectionTimeoutSeconds` and surface as a transient connection
 * error. Once the function instance is warm and the keep-alive socket
 * is open, the same call returns in 100-200ms.
 *
 * Without retries, the provider's outermost `try/catch` swallows the
 * error and returns `emptyResponse()` (`{ companies: [], degraded: true }`).
 * Combined with the page-level `'use cache'` (`cacheLife: 60s`), a
 * single cold-start blip would poison the prerender for the whole
 * region for 60 seconds.
 *
 * Mirror of `apps/web/src/lib/db-retry.ts` — same shape, scoped to the
 * Typesense client error vocabulary instead of postgres.js.
 *
 * Retry policy:
 *   - 3 attempts total (initial + 2 retries)
 *   - Exponential backoff: 200ms / 400ms baseline + 0..100ms jitter
 *   - Retry only on transient connection errors (see `isRetryableError`)
 *   - Non-retryable errors (4xx auth, 400 schema, syntax) propagate
 *     immediately so the upstream `try/catch` returns `emptyResponse()`
 *     without burning the budget
 *   - Structured `external_client_error` warning on every retry
 */

import { logExternalError } from "@/lib/safe-external-error";

const RETRYABLE_NODE_CODES = new Set([
  "ECONNRESET",
  "ETIMEDOUT",
  "ECONNREFUSED",
  "EPIPE",
  "ENOTFOUND",
  "ECONNABORTED",
  "EAI_AGAIN",
]);

/**
 * Substring matches against the Typesense client error message. The
 * Typesense node SDK wraps axios; transient connection-class events
 * surface as one of these strings — there is no dedicated error code.
 *
 *   - "request timed out"           — axios connect/read timeout
 *   - "timeout exceeded"            — Typesense client-internal timeout
 *   - "socket hang up"              — transient socket close mid-request
 *   - "connection reset"            — TCP RST during TLS handshake
 *   - "network error"               — generic axios network class
 *   - "service unavailable"         — Typesense returns 503 during boot
 *   - "request retry"               — Typesense SDK internal retry exhausted
 *
 * 4xx / `Bad Parameter` / auth errors are NOT in this list — they're
 * deterministic and shouldn't waste retry budget.
 */
const RETRYABLE_MESSAGE_FRAGMENTS = [
  "request timed out",
  "timeout exceeded",
  "socket hang up",
  "connection reset",
  "connection terminated",
  "network error",
  "service unavailable",
  "not ready or lagging",
  "request retry",
  "econnreset",
  "etimedout",
  "econnrefused",
];

/**
 * HTTP status codes (when the Typesense client surfaces them on the
 * error) that are retry-worthy. The Typesense SDK wraps responses in
 * named error classes (`ServerError`, `ServiceUnavailable`, etc.) and
 * sets `httpStatus` on them.
 */
const RETRYABLE_HTTP_STATUSES = new Set([502, 503, 504]);

const CONFIG_UNAVAILABLE_MESSAGE_FRAGMENTS = [
  "typesense connection not configured",
  "typesense_search_key is not set",
];

type TypesenseSearchResultShape = {
  found: number;
  hits?: Array<{ document: Record<string, unknown> }>;
};

function safeGet(value: unknown, key: PropertyKey): unknown {
  if ((typeof value !== "object" || value === null) && typeof value !== "function") {
    return undefined;
  }
  try {
    return Reflect.get(value, key);
  } catch {
    return undefined;
  }
}

/**
 * Create a deliberately content-free error for a successful HTTP response
 * whose body does not satisfy the Typesense search contract. Raw upstream
 * bodies must never cross a cache or Server Action boundary.
 */
export function malformedTypesenseResponseError(): Error {
  return Object.assign(new Error("Typesense response was malformed"), {
    typesenseUnavailable: true as const,
  });
}

function typesenseAbortError(): Error {
  return Object.assign(new Error("Typesense request was aborted"), {
    typesenseUnavailable: true as const,
  });
}

function throwIfTypesenseAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) throw typesenseAbortError();
}

/**
 * Runtime guard for SDK search responses. TypeScript's SDK types cannot
 * protect callers from a truncated proxy body or a non-JSON/malformed
 * upstream response, so validate the small shape readers rely on before
 * dereferencing it.
 */
export function assertTypesenseSearchResult(
  value: unknown,
  options: { expectHits?: boolean } = {},
): asserts value is TypesenseSearchResultShape {
  if (typeof value !== "object" || value === null) {
    throw malformedTypesenseResponseError();
  }

  const found = safeGet(value, "found");
  if (
    typeof found !== "number" ||
    !Number.isInteger(found) ||
    found < 0
  ) {
    throw malformedTypesenseResponseError();
  }

  const hits = safeGet(value, "hits");
  if (hits !== undefined && !Array.isArray(hits)) {
    throw malformedTypesenseResponseError();
  }
  if (options.expectHits && found > 0 && !Array.isArray(hits)) {
    throw malformedTypesenseResponseError();
  }
  if (
    Array.isArray(hits) &&
    hits.some((hit) => {
      if (typeof hit !== "object" || hit === null) return true;
      const document = safeGet(hit, "document");
      return typeof document !== "object" || document === null || Array.isArray(document);
    })
  ) {
    throw malformedTypesenseResponseError();
  }
}

function errorNodes(error: unknown): unknown[] {
  const nodes: unknown[] = [];
  const queue: unknown[] = [error];
  const seen = new Set<unknown>();

  while (queue.length > 0 && nodes.length < 8) {
    const node = queue.shift();
    if ((typeof node !== "object" || node === null) && typeof node !== "function") continue;
    if (seen.has(node)) continue;
    seen.add(node);
    nodes.push(node);
    for (const key of ["cause", "response", "originalError"] as const) {
      const nested = safeGet(node, key);
      if (nested !== undefined && nested !== node) queue.push(nested);
    }
  }
  return nodes;
}

function nestedHttpStatus(err: unknown): number | undefined {
  for (const node of errorNodes(err)) {
    for (const key of ["httpStatus", "status", "statusCode"] as const) {
      const value = safeGet(node, key);
      // Axios uses status=0 when no HTTP response was received. Only a real
      // HTTP status is authoritative over its ECONNABORTED/network code.
      if (
        typeof value === "number" &&
        Number.isInteger(value) &&
        value >= 100 &&
        value <= 599
      ) {
        return value;
      }
    }
  }
  return undefined;
}

export function isRetryableError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  // An explicit HTTP response is authoritative. In particular, a 4xx
  // response must not become retryable merely because an SDK wrapper reuses
  // a connection-flavoured message such as "service unavailable".
  const status = nestedHttpStatus(err);
  if (status !== undefined) return RETRYABLE_HTTP_STATUSES.has(status);
  for (const node of errorNodes(err)) {
    // Boundary-sanitized errors discard the original SDK message/config.
    // Retain only this boolean so application-level bounded retries keep
    // working without regaining credential-bearing request state.
    if (safeGet(node, "typesenseRetryable") === true) return true;
    const code = safeGet(node, "code");
    if (typeof code === "string" && RETRYABLE_NODE_CODES.has(code)) return true;
    const message = safeGet(node, "message");
    if (typeof message === "string") {
      const lower = message.toLowerCase();
      for (const frag of RETRYABLE_MESSAGE_FRAGMENTS) {
        if (lower.includes(frag)) return true;
      }
    }
  }
  return false;
}

export function isTypesenseRateLimitError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const status = nestedHttpStatus(err);
  if (status !== undefined) return status === 429;
  for (const node of errorNodes(err)) {
    if (safeGet(node, "typesenseRateLimited") === true) return true;
    const message = safeGet(node, "message");
    if (typeof message === "string" && message.toLowerCase().includes("http code 429")) {
      return true;
    }
  }
  return false;
}

export function isTypesenseUnavailableError(err: unknown): boolean {
  if (isRetryableError(err)) return true;
  if (!err || typeof err !== "object") return false;
  // Preserve the same status precedence as `isRetryableError`: once an SDK
  // object carries a concrete non-retryable response (notably 429), do not
  // recurse into a misleading nested message and classify it as an outage.
  if (nestedHttpStatus(err) !== undefined) return false;
  for (const node of errorNodes(err)) {
    if (safeGet(node, "typesenseUnavailable") === true) return true;
  }
  for (const node of errorNodes(err)) {
    const message = safeGet(node, "message");
    if (typeof message === "string") {
      const lower = message.toLowerCase();
      if (CONFIG_UNAVAILABLE_MESSAGE_FRAGMENTS.some((frag) => lower.includes(frag))) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Strip Axios/SDK request state before an error crosses a Next cache or
 * Server Action boundary. Those objects can retain API-key headers, response
 * bodies and credential-bearing configuration that framework error
 * serialization may otherwise emit independently of application logging.
 */
export function sanitizeTypesenseBoundaryError(err: unknown): Error {
  const sanitized = new Error("Typesense request failed") as Error & {
    code?: string;
    httpStatus?: number;
    typesenseRateLimited?: true;
    typesenseRetryable?: true;
    typesenseUnavailable?: true;
  };
  const rateLimited = isTypesenseRateLimitError(err);
  const retryable = isRetryableError(err);
  const unavailable = isTypesenseUnavailableError(err);
  const status = nestedHttpStatus(err);
  if (status !== undefined) sanitized.httpStatus = status;
  for (const node of errorNodes(err)) {
    const code = safeGet(node, "code");
    if (typeof code === "string" && RETRYABLE_NODE_CODES.has(code)) {
      sanitized.code = code;
      break;
    }
  }
  if (rateLimited) sanitized.typesenseRateLimited = true;
  if (retryable) sanitized.typesenseRetryable = true;
  if (unavailable) sanitized.typesenseUnavailable = true;
  return sanitized;
}

/**
 * Execute one SDK operation and guarantee that only the deliberately lossy
 * error envelope can escape. The client proxy uses this central boundary, so
 * direct SDK calls, retries, Next caches and Server Actions all receive the
 * same credential-free exception object.
 */
export async function withSanitizedTypesenseBoundary<T>(
  operation: () => PromiseLike<T> | T,
): Promise<T> {
  try {
    return await operation();
  } catch (err) {
    throw sanitizeTypesenseBoundaryError(err);
  }
}

export interface TypesenseRetryOptions {
  /** Total attempts (initial + retries). Defaults to 3. */
  attempts?: number;
  /**
   * Base delays in ms before each retry attempt. The N-th retry waits
   * `baseDelaysMs[N-1] + jitter`. Defaults to [200, 400].
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
  /** Stops the active request and prevents any later retry attempt. */
  abortSignal?: AbortSignal;
}

const defaultSleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

async function abortableRetrySleep(
  ms: number,
  sleep: (ms: number) => Promise<void>,
  signal: AbortSignal | undefined,
): Promise<void> {
  if (!signal) {
    await sleep(ms);
    return;
  }
  throwIfTypesenseAborted(signal);

  let onAbort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    onAbort = () => reject(typesenseAbortError());
    signal.addEventListener("abort", onAbort, { once: true });
  });
  try {
    await Promise.race([sleep(ms), aborted]);
  } finally {
    if (onAbort) signal.removeEventListener("abort", onAbort);
  }
  throwIfTypesenseAborted(signal);
}

/**
 * Run `fn`, retrying on transient connection-class errors. Returns the
 * first successful result, or throws the last error after exhausting
 * the attempt budget. The final throw preserves the original exception
 * (no wrapping) so call-site error handling stays unchanged — the
 * `TypesenseSearchProvider` outer `try/catch` still observes the same
 * error vocabulary it always did.
 */
export async function withTypesenseRetry<T>(
  fn: () => Promise<T>,
  opts: TypesenseRetryOptions = {},
): Promise<T> {
  const attempts = opts.attempts ?? 3;
  const baseDelays = opts.baseDelaysMs ?? [200, 400];
  const maxJitter = opts.maxJitterMs ?? 100;
  const sleep = opts.sleep ?? defaultSleep;
  const retryable = opts.isRetryable ?? isRetryableError;
  const label = opts.label ?? "typesense";

  let lastErr: unknown;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    throwIfTypesenseAborted(opts.abortSignal);
    try {
      return await fn();
    } catch (err) {
      throwIfTypesenseAborted(opts.abortSignal);
      lastErr = err;
      const isLast = attempt >= attempts;
      if (isLast || !retryable(err)) {
        throw err;
      }
      const baseDelay =
        baseDelays[attempt - 1] ?? baseDelays[baseDelays.length - 1] ?? 200;
      const jitter = Math.floor(Math.random() * (maxJitter + 1));
      const delay = baseDelay + jitter;
      logExternalError(
        "warn",
        {
          service: "typesense",
          operation: `${label}_retry`,
          retryCount: attempt,
        },
        err,
      );
      await abortableRetrySleep(delay, sleep, opts.abortSignal);
    }
  }
  // Unreachable: the loop either returns or throws. Re-throw lastErr to
  // satisfy the type checker.
  throw lastErr;
}
