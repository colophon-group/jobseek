import { parseSearchFilters } from "@/lib/services/search-input";

export async function GET(request: Request) {
  const url = new URL(request.url);
  if (process.env.NODE_ENV !== "development" || !["localhost", "127.0.0.1"].includes(url.hostname)) {
    return new Response(null, { status: 404 });
  }
  const q = url.searchParams.get("q") ?? "";
  const locale = url.searchParams.get("locale") ?? "en";
  const started = performance.now();
  const parsed = await parseSearchFilters({ q, locale });
  return Response.json({ parserMs: Math.round(performance.now() - started), parsed }, {
    headers: { "Cache-Control": "no-store" },
  });
}
