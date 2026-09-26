import { parseSearchFilters } from "@/lib/services/search-input";
import { suggestLocations } from "@/lib/services/locations";
import {
  suggestOccupations,
  suggestSeniorities,
  suggestTechnologies,
} from "@/lib/services/taxonomy";

function localDevelopment(request: Request) {
  const url = new URL(request.url);
  return process.env.NODE_ENV === "development" && ["localhost", "127.0.0.1"].includes(url.hostname);
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  if (!localDevelopment(request)) {
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

type CandidateRequest = { key: string; category: string; text: string };

export async function POST(request: Request) {
  if (!localDevelopment(request)) return new Response(null, { status: 404 });
  const body = await request.json() as { locale?: string; requests?: CandidateRequest[] };
  const locale = body.locale ?? "en";
  const requests = body.requests ?? [];
  const allowed = new Set(["location", "occupation", "seniority", "technology"]);
  if (
    !["en", "de", "fr", "it"].includes(locale) ||
    requests.length > 64 ||
    requests.some((item) =>
      !item || typeof item.key !== "string" || item.key.length > 80 ||
      typeof item.text !== "string" || item.text.length > 120 ||
      !allowed.has(item.category))
  ) return new Response(null, { status: 400 });

  const results: Record<string, unknown> = {};
  // Bound concurrent cache misses so the experiment does not exceed the
  // Typesense tunnel's per-client burst allowance.
  try {
    for (let offset = 0; offset < requests.length; offset += 6) {
      const batch = requests.slice(offset, offset + 6);
      const suggestions = await Promise.all(batch.map(async (item) => {
        const params = { query: item.text, locale, failOnUnavailable: true };
        if (item.category === "location") return suggestLocations(params);
        if (item.category === "occupation") return suggestOccupations(params);
        if (item.category === "seniority") return suggestSeniorities(params);
        return suggestTechnologies(params);
      }));
      batch.forEach((item, index) => { results[item.key] = suggestions[index]; });
    }
  } catch {
    return new Response(null, { status: 503 });
  }
  return Response.json({ results }, { headers: { "Cache-Control": "no-store" } });
}
