import { siteConfig } from "@/content/config";
import { locales } from "@/lib/i18n";

const INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow";

/**
 * Submit one or more app paths to IndexNow.
 *
 * Each path is expanded to every supported locale prefix — a single
 * user-facing mutation covers all hreflang alternates. A single POST
 * to `api.indexnow.org` propagates to Bing, Yandex, Seznam, Naver, and
 * Microsoft Yep. Google does not participate in IndexNow.
 *
 * **Caller responsibility**: invoke from inside an `after()` block in
 * the originating server action / route handler. This function awaits
 * the fetch directly; without `after()`, the unawaited promise is
 * detached and Vercel may terminate the function before the POST
 * completes.
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
 * @param paths Locale-less app paths, e.g. ["/user/watchlist-slug"].
 */
export async function notifyIndexNow(paths: string[]): Promise<void> {
  const key = process.env.INDEXNOW_KEY;
  if (!key || paths.length === 0) return;

  const urlList: string[] = [];
  for (const path of paths) {
    const encoded = encodePath(path);
    for (const locale of locales) {
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
