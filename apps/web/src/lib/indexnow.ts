import { after } from "next/server";
import { siteConfig } from "@/content/config";
import { locales } from "@/lib/i18n";

const INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow";

/**
 * Deferred IndexNow notification for one or more app paths.
 *
 * Each path is expanded to every supported locale prefix — a single
 * user-facing mutation covers all hreflang alternates. A single POST
 * to `api.indexnow.org` propagates to Bing, Yandex, Seznam, Naver, and
 * Microsoft Yep. Google does not participate in IndexNow.
 *
 * Uses `after()` from `next/server`: the callback runs after the server
 * action's response has been streamed, but before the serverless
 * invocation is allowed to terminate. This avoids the classic
 * "fire-and-forget promise gets killed when the function returns"
 * failure mode on Vercel.
 *
 * No-op when `INDEXNOW_KEY` is unset (local dev, preview deploys
 * without the secret). All errors are swallowed and logged — callers
 * must never observe failures.
 *
 * @param paths Locale-less app paths, e.g. ["/user/watchlist-slug"].
 */
export function notifyIndexNow(paths: string[]): void {
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

  after(async () => {
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
  });
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
