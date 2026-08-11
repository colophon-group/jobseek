import "server-only";

import { cacheLife } from "next/cache";
import {
  companyOgCompletionUrl,
  companyOgPublicUrl,
} from "@/lib/og/company-og-key";

type CompletionMarker = {
  complete?: unknown;
  rendererVersion?: unknown;
  sourceVersion?: unknown;
};

export async function checkCompanyOgNamespaceComplete(
  domain: string,
  rendererVersion: string,
  sourceVersion: string,
  fetcher: typeof fetch = fetch,
): Promise<boolean> {
  const markerUrl = companyOgCompletionUrl(
    domain,
    rendererVersion,
    sourceVersion,
  );
  if (!markerUrl) return false;

  try {
    const response = await fetcher(markerUrl, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return false;
    const marker = await response.json() as CompletionMarker;
    return marker.complete === true &&
      marker.rendererVersion === rendererVersion &&
      marker.sourceVersion === sourceVersion;
  } catch {
    return false;
  }
}

async function isConfiguredCompanyOgNamespaceComplete(
  domain: string,
  rendererVersion: string,
  sourceVersion: string,
): Promise<boolean> {
  "use cache";
  cacheLife({ revalidate: 300 });
  return checkCompanyOgNamespaceComplete(
    domain,
    rendererVersion,
    sourceVersion,
  );
}

/** Return a direct R2 URL only after the full source/version matrix is ready. */
export async function getDirectCompanyOgUrl(
  locale: string,
  slug: string,
): Promise<string | null> {
  const domain = process.env.R2_DOMAIN_URL;
  const rendererVersion = process.env.COMPANY_OG_RENDERER_VERSION;
  const sourceVersion = process.env.COMPANY_OG_SOURCE_VERSION;
  if (!domain || !rendererVersion || !sourceVersion) return null;

  const complete = await isConfiguredCompanyOgNamespaceComplete(
    domain,
    rendererVersion,
    sourceVersion,
  );
  if (!complete) return null;

  return companyOgPublicUrl(
    domain,
    rendererVersion,
    locale,
    slug,
    sourceVersion,
  );
}
