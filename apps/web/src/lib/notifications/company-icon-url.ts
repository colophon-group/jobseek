const ASSET_ORIGIN = "https://jobseek-assets.colophon-group.org";
const COMPANY_IMAGE_PATH = /^\/companies\/[a-z0-9][a-z0-9-]*\/(?:icon|logo)(?:-[a-f0-9]{64})?\.(?:webp|svg|png|jpe?g|gif)$/;

/** Only public company artwork on our asset host may be fetched by the converter. */
export function emailCompanyIconSource(value: string): URL | null {
  if (value.length > 512) return null;
  try {
    const url = new URL(value);
    if (url.origin !== ASSET_ORIGIN || url.username || url.password || url.search || url.hash ||
      !COMPANY_IMAGE_PATH.test(url.pathname) || url.href !== value) return null;
    return url;
  } catch { return null; }
}

export function emailCompanyIconUrl(icon: string, origin: string): string {
  const source = emailCompanyIconSource(icon);
  if (!source) return icon;
  const url = new URL("/api/notifications/company-icon/v1.png", origin);
  url.searchParams.set("src", source.href);
  return url.href;
}
