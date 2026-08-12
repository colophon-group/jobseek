function segment(value: string): string {
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9-]/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 120) || "unknown"
  );
}

export function companyOgCacheKeyForVersion(
  rendererVersion: string,
  locale: string,
  slug: string,
): string {
  return `og/company/${segment(rendererVersion)}/${segment(locale)}/${segment(slug)}.png`;
}

export function companyOgCompletionKeyForVersion(
  rendererVersion: string,
  sourceVersion: string,
): string {
  return `og/company/${segment(rendererVersion)}/_complete/${segment(sourceVersion)}.json`;
}

export function companyOgCurrentCompletionKey(
  rendererVersion: string,
): string {
  return `og/company/${segment(rendererVersion)}/_complete/current.json`;
}

export function companyOgCacheKey(locale: string, slug: string): string {
  const rendererVersion =
    process.env.COMPANY_OG_RENDERER_VERSION ||
    process.env.VERCEL_GIT_COMMIT_SHA?.slice(0, 16) ||
    "local";
  return companyOgCacheKeyForVersion(rendererVersion, locale, slug);
}

export function companyOgPublicUrl(
  domain: string,
  rendererVersion: string,
  locale: string,
  slug: string,
  sourceVersion?: string,
): string | null {
  try {
    const base = new URL(domain.endsWith("/") ? domain : `${domain}/`);
    if (base.protocol !== "https:") return null;
    const key = companyOgCacheKeyForVersion(rendererVersion, locale, slug);
    const url = new URL(
      key.split("/").map(encodeURIComponent).join("/"),
      base,
    );
    if (sourceVersion) url.searchParams.set("v", segment(sourceVersion));
    return url.toString();
  } catch {
    return null;
  }
}

export function companyOgCompletionUrl(
  domain: string,
  rendererVersion: string,
  sourceVersion: string,
): string | null {
  try {
    const base = new URL(domain.endsWith("/") ? domain : `${domain}/`);
    if (base.protocol !== "https:") return null;
    const key = companyOgCompletionKeyForVersion(
      rendererVersion,
      sourceVersion,
    );
    return new URL(
      key.split("/").map(encodeURIComponent).join("/"),
      base,
    ).toString();
  } catch {
    return null;
  }
}

export function companyOgCurrentCompletionUrl(
  domain: string,
  rendererVersion: string,
): string | null {
  try {
    const base = new URL(domain.endsWith("/") ? domain : `${domain}/`);
    if (base.protocol !== "https:") return null;
    const key = companyOgCurrentCompletionKey(rendererVersion);
    return new URL(
      key.split("/").map(encodeURIComponent).join("/"),
      base,
    ).toString();
  } catch {
    return null;
  }
}
