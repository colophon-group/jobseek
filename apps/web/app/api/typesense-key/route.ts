import { connection } from "next/server";
import { generateScopedSearchKey } from "@/lib/search/scoped-key";

const KEY_TTL_SECONDS = 600;
// Leave 90 seconds between the Vercel CDN freshness boundary and the signed
// key expiry. The browser refreshes 30 seconds early, so even a near-boundary
// cache hit remains usable without immediately fetching another key.
const VERCEL_CDN_TTL_SECONDS = 510;

export async function GET() {
  // cacheComponents may otherwise evaluate Date.now() while prerendering.
  // The response is generated at request time, then shared by Vercel's CDN.
  await connection();

  // Typesense scoped keys must be derived from a parent whose actions list is
  // exactly ["documents:search"]. The regular TYPESENSE_SEARCH_KEY also carries
  // documents:get, so the server rejects scoped keys minted from it.
  // TYPESENSE_BROWSER_PARENT_KEY is a dedicated documents:search-only key used
  // only for browser-side scoped-key minting.
  const parentKey = process.env.TYPESENSE_BROWSER_PARENT_KEY;
  const host = process.env.TYPESENSE_HOST;
  const port = process.env.TYPESENSE_PORT;
  const protocol = process.env.TYPESENSE_PROTOCOL;
  if (!parentKey || !host || !port || !protocol) {
    return Response.json({ error: "search not configured" }, { status: 503 });
  }

  // The browser key grants the same search-only collection scope to every
  // visitor. Keeping this route session-independent avoids pulling the auth,
  // database, and Redis dependency graph into a hot Function and makes its
  // successful response safe to share at the CDN.
  const expiresAtSeconds = Math.floor(Date.now() / 1000) + KEY_TTL_SECONDS;

  // limit_hits is intentionally omitted: it counts raw hits (not grouped rows),
  // so it would block normal anon traffic that uses group_by company_id with
  // group_limit 10. Anon truncation is enforced as a soft client-side cap; the
  // Cloudflare per-IP rate-limit on typesense.colophon-group.org is the real
  // abuse brake.
  const apiKey = generateScopedSearchKey(parentKey, {
    use_cache: true,
    expires_at: expiresAtSeconds,
  });

  // Keep browser metadata on the exact signed boundary. Typesense validates
  // expires_at as Unix seconds while the browser cache consumes milliseconds.
  const expiresAt = expiresAtSeconds * 1000;

  return Response.json(
    {
      apiKey,
      expiresAt,
      host,
      port: Number.parseInt(port, 10),
      protocol,
    },
    {
      headers: {
        // Keep browsers in control of their short-lived local config cache;
        // this header is forwarded unchanged to clients.
        "cache-control": "public, max-age=0, must-revalidate",
        // Vercel consumes this header and does not forward it to clients.
        // A dedicated header makes the shared-cache contract explicit instead
        // of relying on s-maxage stripping from Cache-Control.
        "vercel-cdn-cache-control": `public, s-maxage=${VERCEL_CDN_TTL_SECONDS}`,
      },
    },
  );
}
