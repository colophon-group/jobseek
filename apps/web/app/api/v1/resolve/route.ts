import { type NextRequest } from "next/server";
import { suggestLocations } from "@/lib/actions/locations";
import {
  suggestOccupations,
  suggestSeniorities,
  suggestTechnologies,
} from "@/lib/actions/taxonomy";
import { suggestIndustries } from "@/lib/actions/company";
import { checkRateLimit, apiResponse } from "../_shared";

const VALID_TYPES = [
  "locations",
  "occupations",
  "seniority",
  "technologies",
  "industries",
] as const;

export async function GET(request: NextRequest) {
  const limited = await checkRateLimit(request);
  if (limited) return limited;

  const sp = request.nextUrl.searchParams;
  const type = sp.get("type") as (typeof VALID_TYPES)[number] | null;
  const q = sp.get("q");
  const locale = sp.get("locale") ?? "en";

  if (!type || !VALID_TYPES.includes(type)) {
    return apiResponse(
      {
        error: `Missing or invalid 'type' param. Valid: ${VALID_TYPES.join(", ")}`,
      },
      { maxAge: 0 },
    );
  }

  if (!q || q.trim().length < 2) {
    return apiResponse(
      { error: "Missing or too short 'q' param (min 2 chars)" },
      { maxAge: 0 },
    );
  }

  let matches: { slug: string; name: string; type?: string; parentName?: string | null }[];

  switch (type) {
    case "locations": {
      const data = await suggestLocations({ query: q, locale });
      matches = data.map((l) => ({
        slug: l.slug,
        name: l.name,
        type: l.type,
        parentName: l.parentName,
      }));
      break;
    }
    case "occupations": {
      const data = await suggestOccupations({ query: q, locale });
      matches = data.map((o) => ({ slug: o.slug, name: o.name }));
      break;
    }
    case "seniority": {
      const data = await suggestSeniorities({ query: q, locale });
      matches = data.map((s) => ({ slug: s.slug, name: s.name }));
      break;
    }
    case "technologies": {
      const data = await suggestTechnologies({ query: q, locale });
      matches = data.map((t) => ({ slug: t.slug, name: t.name }));
      break;
    }
    case "industries": {
      const data = await suggestIndustries({ query: q, locale });
      matches = data.map((i) => ({ slug: i.slug, name: i.name }));
      break;
    }
  }

  return apiResponse({
    type,
    query: q,
    matches: matches.slice(0, 10),
  });
}
