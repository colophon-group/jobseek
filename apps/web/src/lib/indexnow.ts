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
export async function notifyIndexNow(
  paths: string[],
  availableLocales?: readonly Locale[],
): Promise<void> {
  const key = process.env.INDEXNOW_KEY;
  if (!key || paths.length === 0) return;

  const targetLocales = availableLocales ?? locales;
  if (targetLocales.length === 0) return;

  const urlList: string[] = [];
  for (const path of paths) {
    const encoded = encodePath(path);
    for (const locale of targetLocales) {
      urlList.push(`${siteConfig.url}/${locale}${encoded}`);
    }
  }

  // Dedupe and cap at the protocol limit (10k per request).
  const unique = [...new Set(urlList)].slice(0, 10_000);
  if (unique.length === 0) return;

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
    }
  } catch (err) {
    console.error("[indexnow] submission failed", err);
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
