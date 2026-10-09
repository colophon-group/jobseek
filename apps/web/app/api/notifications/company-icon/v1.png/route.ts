import { emailCompanyIconSource } from "@/lib/notifications/company-icon-url";
import { renderEmailCompanyIcon } from "@/lib/notifications/render-company-icon";

const MAX_SOURCE_BYTES = 5 * 1024 * 1024;

function failure(status: number): Response {
  return new Response(null, { status, headers: { "Cache-Control": "no-store" } });
}

/** Public PNG artwork only; no session or notification-delivery state is read. */
export async function GET(request: Request): Promise<Response> {
  const query = new URL(request.url).searchParams;
  const source = emailCompanyIconSource(query.get("src") ?? "");
  if (query.size !== 1 || !source) return failure(400);
  try {
    const upstream = await fetch(source, {
      redirect: "error", signal: AbortSignal.timeout(10_000),
    });
    if (!upstream.ok || !upstream.body) return failure(502);
    if (Number(upstream.headers.get("content-length")) > MAX_SOURCE_BYTES) {
      await upstream.body.cancel();
      return failure(502);
    }
    const reader = upstream.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > MAX_SOURCE_BYTES) {
          await reader.cancel();
          return failure(502);
        }
        chunks.push(value);
      }
    } finally { reader.releaseLock(); }
    const png = await renderEmailCompanyIcon(Buffer.concat(chunks));
    // Hashed sources are immutable. Historical icon.webp paths may be replaced.
    const cacheControl = /-[a-f0-9]{64}\./.test(source.pathname)
      ? "public, max-age=31536000, s-maxage=31536000, immutable"
      : "public, max-age=86400, s-maxage=86400";
    return new Response(new Uint8Array(png), { headers: {
      "Content-Type": "image/png", "Cache-Control": cacheControl,
      "X-Content-Type-Options": "nosniff",
    } });
  } catch { return failure(502); }
}
