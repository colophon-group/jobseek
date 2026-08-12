import "server-only";

import { cacheLife } from "next/cache";
import {
  companyOgCompletionUrl,
  companyOgCurrentCompletionUrl,
  companyOgPublicUrl,
} from "@/lib/og/company-og-key";

type CompletionMarker = {
  complete?: unknown;
  rendererVersion?: unknown;
  sourceVersion?: unknown;
};

function validSourceVersion(value: unknown): value is string {
  return typeof value === "string" && /^[a-z0-9-]{1,120}$/.test(value);
}

async function fetchCompletionMarker(
  markerUrl: string,
  fetcher: typeof fetch,
): Promise<CompletionMarker | null> {
  try {
    const response = await fetcher(markerUrl, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return null;
    return await response.json() as CompletionMarker;
  } catch {
    return null;
  }
}

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

  const marker = await fetchCompletionMarker(markerUrl, fetcher);
  return marker?.complete === true &&
    marker.rendererVersion === rendererVersion &&
    marker.sourceVersion === sourceVersion;
}

/** Resolve the latest fully uploaded source set without coupling it to a build. */
export async function getCurrentCompanyOgSourceVersion(
  domain: string,
  rendererVersion: string,
  fetcher: typeof fetch = fetch,
): Promise<string | null> {
  const markerUrl = companyOgCurrentCompletionUrl(domain, rendererVersion);
  if (!markerUrl) return null;

  const marker = await fetchCompletionMarker(markerUrl, fetcher);
  if (
    marker?.complete !== true ||
    marker.rendererVersion !== rendererVersion ||
    !validSourceVersion(marker.sourceVersion)
  ) {
    return null;
  }
  return marker.sourceVersion;
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

async function getConfiguredCompanyOgSourceVersion(
  domain: string,
  rendererVersion: string,
): Promise<string | null> {
  "use cache";
  cacheLife({ revalidate: 300 });
  return getCurrentCompanyOgSourceVersion(domain, rendererVersion);
}

/** Return a direct R2 URL only after the full source/version matrix is ready. */
export async function getDirectCompanyOgUrl(
  locale: string,
  slug: string,
): Promise<string | null> {
  const domain = process.env.R2_DOMAIN_URL;
  const rendererVersion = process.env.COMPANY_OG_RENDERER_VERSION;
  const fallbackSourceVersion = process.env.COMPANY_OG_SOURCE_VERSION;
  if (!domain || !rendererVersion) return null;

  const currentSourceVersion = await getConfiguredCompanyOgSourceVersion(
    domain,
    rendererVersion,
  );
  const sourceVersion = currentSourceVersion ?? fallbackSourceVersion;
  if (!sourceVersion) return null;

  // The mutable pointer is published only after its source-versioned marker.
  // Keep the build-time source as a fail-safe during rollout or an R2 outage.
  if (!currentSourceVersion) {
    const complete = await isConfiguredCompanyOgNamespaceComplete(
      domain,
      rendererVersion,
      sourceVersion,
    );
    if (!complete) return null;
  }

  return companyOgPublicUrl(
    domain,
    rendererVersion,
    locale,
    slug,
    sourceVersion,
  );
}
