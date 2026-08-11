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
): string | null {
  try {
    const base = new URL(domain.endsWith("/") ? domain : `${domain}/`);
    if (base.protocol !== "https:") return null;
    const key = companyOgCacheKeyForVersion(rendererVersion, locale, slug);
    return new URL(key.split("/").map(encodeURIComponent).join("/"), base).toString();
  } catch {
    return null;
  }
}
