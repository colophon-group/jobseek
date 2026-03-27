import { type NextRequest, NextResponse } from "next/server";
import {
  getAllSeniorities,
  getAllOccupationsGrouped,
  getAllTechnologiesGrouped,
} from "@/lib/actions/taxonomy";
import { suggestIndustries } from "@/lib/actions/company";
import { checkRateLimit, apiResponse } from "../_shared";

const VALID_TYPES = ["seniority", "occupations", "technologies", "industries"] as const;

export async function GET(request: NextRequest) {
  const rl = await checkRateLimit(request);
  if (rl instanceof NextResponse) return rl;

  const sp = request.nextUrl.searchParams;
  const type = sp.get("type") as (typeof VALID_TYPES)[number] | null;
  const locale = sp.get("locale") ?? "en";

  if (!type || !VALID_TYPES.includes(type)) {
    return apiResponse(
      { error: `Missing or invalid 'type' param. Valid: ${VALID_TYPES.join(", ")}` },
      { maxAge: 0 },
    );
  }

  let items: unknown;

  switch (type) {
    case "seniority": {
      const data = await getAllSeniorities(locale);
      items = data.map((s) => ({ slug: s.slug, name: s.name }));
      break;
    }
    case "occupations": {
      const data = await getAllOccupationsGrouped(locale);
      items = data;
      break;
    }
    case "technologies": {
      const data = await getAllTechnologiesGrouped();
      items = data;
      break;
    }
    case "industries": {
      const data = await suggestIndustries({ query: "", locale });
      items = data.map((i) => ({ id: i.id, name: i.name }));
      break;
    }
  }

  return apiResponse({ type, items }, { maxAge: 3600, rateLimit: rl });
}
