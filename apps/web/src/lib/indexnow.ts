import { siteConfig } from "@/content/config";
import { type Locale, locales } from "@/lib/i18n";

const INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow";

/**
 * Submit one or more app paths to IndexNow.
 *
 * Each path is expanded to every locale in `availableLocales` (default:
 * all four supported locales). Pass a per-call subset for partially-
 * translated routes — blog posts whose `getBlogPostLocales(slug)` is a
 * proper subset of `locales`, for example — so engines aren't pointed
 * at locale variants that 404 (or worse, serve the canonical body
 * under a foreign-locale URL: an "alternate page with proper canonical
 * tag" cluster).
 *
 * A single POST to `api.indexnow.org` propagates to Bing, Yandex,
 * Seznam, Naver, and Microsoft Yep. Google does not participate in
 * IndexNow.
 *
 * **Caller contract**: this function awaits its fetch directly. In a
 * Vercel server action / route handler, the caller should invoke it
 * from inside `after()` (next/server) so the work survives the
 * response being flushed without blocking it. In contexts where you
 * can simply `await` to completion (route handlers that don't need to
 * stream early, scripts, tests), no wrapping is required.
 *
 * Earlier revisions wrapped the fetch in `after()` internally, but
 * call sites were chaining `notifyIndexNow` off detached
 * `_getOwnerInfo(...).then(...)` promises — by the time the chain
 * resolved the request scope was gone and the inner `after()` no
 * longer registered anything. Pulling `after()` up to the call site
 * keeps the registration synchronous with the request.
 *
 * No-op when `INDEXNOW_KEY` is unset (local dev, preview deploys
 * without the secret) or when `paths` is empty. All errors are caught
 * and logged — callers must never observe failures.
 *
 * @param paths Locale-less app paths, e.g. `["/user/watchlist-slug"]`
 *              or `["/blog/welcome-to-the-job-seek-blog"]`.
 * @param availableLocales Restrict the locale fan-out for these paths.
 *                         Defaults to every supported locale (correct
 *                         for fully-translated surfaces like watchlist
 *                         pages); pass a subset for routes whose
 *                         hreflang map is per-call.
 */
/**
 * Result envelope returned by `notifyIndexNow`. Existing watchlist
 * call sites ignore the return (fire-and-forget inside `after()`);
 * the blog deploy-hook script reads it to fail the workflow on a
 * non-2xx submission so we get an actionable signal instead of a
 * silent stderr line in a stack we never read.
 */
export type NotifyIndexNowResult =
  | { kind: "submitted"; urlCount: number; status: number }
  | { kind: "skipped"; reason: "no-key" | "no-paths" | "no-locales" | "no-urls" }
  | { kind: "rejected"; status: number; urlCount: number }
  | { kind: "errored"; error: unknown; urlCount: number };

export async function notifyIndexNow(
  paths: string[],
  availableLocales?: readonly Locale[],
): Promise<NotifyIndexNowResult> {
  const key = process.env.INDEXNOW_KEY;
  if (!key) return { kind: "skipped", reason: "no-key" };
  if (paths.length === 0) return { kind: "skipped", reason: "no-paths" };

  const targetLocales = availableLocales ?? locales;
  if (targetLocales.length === 0) return { kind: "skipped", reason: "no-locales" };

  const urlList: string[] = [];
  for (const path of paths) {
    const encoded = encodePath(path);
    for (const locale of targetLocales) {
      urlList.push(`${siteConfig.url}/${locale}${encoded}`);
    }
  }

  // Dedupe and cap at the protocol limit (10k per request).
  const unique = [...new Set(urlList)].slice(0, 10_000);
  if (unique.length === 0) return { kind: "skipped", reason: "no-urls" };

  const payload = {
    host: siteConfig.domain,
    key,
    keyLocation: `${siteConfig.url}/indexnow-key.txt`,
    urlList: unique,
  };

  try {
    const res = await fetch(INDEXNOW_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(10_000),
    });
    if (res.status !== 200 && res.status !== 202) {
      console.error(
        `[indexnow] submission rejected (${res.status}) for ${unique.length} urls`,
      );
      return { kind: "rejected", status: res.status, urlCount: unique.length };
    }
    return { kind: "submitted", status: res.status, urlCount: unique.length };
  } catch (err) {
    console.error("[indexnow] submission failed", err);
    return { kind: "errored", error: err, urlCount: unique.length };
  }
}

/**
 * encodeURIComponent each slash-delimited segment of a path. Guards
 * against usernames / slugs with spaces, unicode, or already-encoded
 * characters that would otherwise get mangled by string concatenation.
 */
function encodePath(path: string): string {
  return path
    .split("/")
    .map((segment) => (segment ? encodeURIComponent(segment) : segment))
    .join("/");
}

/**
 * Stable event name used by every structured log line emitted from
 * `logIndexNowResult`. Filter Vercel logs by this prefix to aggregate
 * IndexNow outcomes across all watchlist call sites (#3202).
 */
export const INDEXNOW_LOG_EVENT = "indexnow.result";

/**
 * Hostname of the IndexNow aggregator. The IndexNow protocol uses a
 * single endpoint that fans out to Bing, Yandex, Seznam, Naver, and
 * Microsoft Yep — there is no per-engine status code returned to the
 * submitter, so the `host` field is necessarily the aggregator and
 * the status code is whatever it returns (typically 200/202 on accept,
 * 4xx on key/quota issues). The blog-side script in
 * `apps/web/script/notify-blog-indexnow.ts` makes the same trade-off.
 */
const INDEXNOW_HOST = "api.indexnow.org";

/**
 * Emit a single structured log line for an IndexNow result, so the
 * rejection rate / skip rate / error rate can be aggregated from
 * Vercel function logs (#3202). Previously, every watchlist call site
 * discarded the `NotifyIndexNowResult` envelope — a 422 storm from
 * Yandex after a key rotation was invisible to operators, and a
 * `skipped: "no-key"` on a preview deploy without `INDEXNOW_KEY` was
 * silent. Pairs with `notifyIndexNow`'s already-correct return shape.
 *
 * Log level is chosen so production filtering surfaces only the
 * actionable cases by default:
 *
 *   - `submitted` → `console.info`  (normal path, low signal)
 *   - `skipped`   → `console.debug` (no-op; expected on previews)
 *   - `rejected`  → `console.warn`  (HTTP non-2xx — actionable)
 *   - `errored`   → `console.warn`  (network/abort — actionable)
 *
 * All lines start with the stable event name {@link INDEXNOW_LOG_EVENT}
 * (`"indexnow.result"`) as their first argument so a single Vercel log
 * filter catches every outcome. The structured payload follows as the
 * second argument; Vercel's log viewer preserves it as JSON.
 *
 * @param label Short call-site identifier — e.g. `"createWatchlist"`.
 *              Surfaces in the log line so operators can attribute a
 *              rejection storm to the right server action without
 *              digging through stack traces.
 * @param result The envelope returned by `notifyIndexNow`.
 */
export function logIndexNowResult(
  label: string,
  result: NotifyIndexNowResult,
): void {
  switch (result.kind) {
    case "submitted":
      console.info(INDEXNOW_LOG_EVENT, {
        label,
        kind: "submitted",
        status: result.status,
        urlCount: result.urlCount,
        host: INDEXNOW_HOST,
      });
      return;
    case "skipped":
      console.debug(INDEXNOW_LOG_EVENT, {
        label,
        kind: "skipped",
        reason: result.reason,
        urlCount: 0,
      });
      return;
    case "rejected":
      console.warn(INDEXNOW_LOG_EVENT, {
        label,
        kind: "rejected",
        status: result.status,
        urlCount: result.urlCount,
        host: INDEXNOW_HOST,
      });
      return;
    case "errored":
      console.warn(INDEXNOW_LOG_EVENT, {
        label,
        kind: "errored",
        error: result.error instanceof Error ? result.error.message : String(result.error),
        urlCount: result.urlCount,
      });
      return;
  }
}
