import { NextResponse } from "next/server";
import { generateScopedSearchKey } from "@/lib/search/scoped-key";
import { getSessionUserId } from "@/lib/sessionCache";

const ANON_TTL_SECONDS = 300;
const AUTHED_TTL_SECONDS = 600;

export const runtime = "nodejs";

export async function GET() {
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
    return NextResponse.json({ error: "search not configured" }, { status: 503 });
  }

  const userId = await getSessionUserId();
  const ttl = userId ? AUTHED_TTL_SECONDS : ANON_TTL_SECONDS;

  // limit_hits is intentionally omitted: it counts raw hits (not grouped rows),
  // so it would block normal anon traffic that uses group_by company_id with
  // group_limit 10. Anon truncation is enforced as a soft client-side cap; the
  // Cloudflare per-IP rate-limit on typesense.colophon-group.org is the real
  // abuse brake.
  const apiKey = generateScopedSearchKey(parentKey, { use_cache: true });

  const expiresAt = Date.now() + ttl * 1000;
  const cacheControl = userId
    ? `private, max-age=${Math.floor(ttl / 2)}`
    : `public, s-maxage=${Math.floor(ttl / 2)}, max-age=0`;

  return NextResponse.json(
    {
      apiKey,
      expiresAt,
      host,
      port: Number.parseInt(port, 10),
      protocol,
    },
    {
      headers: { "cache-control": cacheControl },
    },
  );
}
