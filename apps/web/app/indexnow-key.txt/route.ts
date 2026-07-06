import { connection } from "next/server";

// IndexNow verification endpoint. Participating engines (Bing, Yandex,
// Seznam, Naver, Microsoft Yep) HEAD/GET this URL to verify ownership
// before accepting URL submissions. The `keyLocation` field in each
// submission payload points here, so the key is never baked into the URL.
//
// `await connection()` opts the route into dynamic execution under
// cacheComponents — without it, the build prerenders the file with the
// build-time INDEXNOW_KEY baked in, and rotating the secret would
// require a redeploy. The `Cache-Control: no-store` response header
// keeps downstream caches from holding the old value.
//
// Returns 404 if INDEXNOW_KEY is unset — matches the notifier's own
// short-circuit so absence is consistent end-to-end.

export async function GET() {
  await connection();
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
