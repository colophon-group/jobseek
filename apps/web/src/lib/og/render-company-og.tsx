import "server-only";

import { cacheLife } from "next/cache";
import {
  renderCompanyOgCard,
  renderCompanyOgNotFoundCard,
} from "@/lib/og/company-og-card";
import {
  getCompanyBySlug,
  type CompanyDetail,
} from "@/lib/services/company";

export {
  getCompanyOgFallbackInitials,
  getCompanyOgIconRenderModel,
  getRenderableCompanyOgIconUrl,
} from "@/lib/og/company-og-card";

const COMPANY_OG_CACHE_TTL_SECONDS = 2592000;

async function getCachedOgCompany(
  slug: string,
  lang: string,
): Promise<CompanyDetail | null> {
  "use cache";
  cacheLife({ revalidate: COMPANY_OG_CACHE_TTL_SECONDS });
  return getCompanyBySlug(slug, lang);
}

export async function renderCompanyOgImage(
  slug: string,
  lang: string,
): Promise<{ response: Response; cacheable: boolean }> {
  const company = await getCachedOgCompany(slug, lang);
  if (!company) {
    return { response: renderCompanyOgNotFoundCard(), cacheable: false };
  }

  return { response: renderCompanyOgCard(company), cacheable: true };
}
