// IndexNow verification endpoint. Participating engines (Bing, Yandex,
// Seznam, Naver, Microsoft Yep) HEAD/GET this URL to verify ownership
// before accepting URL submissions. The `keyLocation` field in each
// submission payload points here, so the key is never baked into the URL.
//
// Must be fully dynamic: we read the env var at request time so rotating
// `INDEXNOW_KEY` takes effect on the next request, not the next deploy.
// `force-static` would bake the build-time value into the prerendered
// HTML and break rotation (and 404 on any preview deploy that was built
// without the secret).
//
// Returns 404 if INDEXNOW_KEY is unset — matches the notifier's own
// short-circuit so absence is consistent end-to-end.

export const dynamic = "force-dynamic";
export const revalidate = 0;

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
