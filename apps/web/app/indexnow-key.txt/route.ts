// IndexNow verification endpoint. Participating engines (Bing, Yandex,
// Seznam, Naver, Microsoft Yep) HEAD/GET this URL to verify ownership
// before accepting URL submissions. The `keyLocation` field in each
// submission payload points here, so the key is never baked into the URL.
//
// Reads `INDEXNOW_KEY` from process.env at request time so a rotation
// takes effect on the next request, not the next deploy. The
// `Cache-Control: no-store` response header prevents downstream caches
// from holding the old value. Route handlers default to dynamic
// execution under cacheComponents, so no segment config is needed.
//
// Returns 404 if INDEXNOW_KEY is unset — matches the notifier's own
// short-circuit so absence is consistent end-to-end.

export function GET() {
  const key = process.env.INDEXNOW_KEY;
  if (!key) return new Response("Not Found", { status: 404 });
  return new Response(key, {
    status: 200,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}
