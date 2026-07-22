const LOCALE_PREFIX = /^\/(?:en|de|fr|it)(?=\/|$)/;

/** Accept only same-origin path/query/hash values for post-auth navigation. */
export function normalizeAuthReturnPath(value: string | null | undefined): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
    return null;
  }

  try {
    const url = new URL(value, "https://jseek.invalid");
    if (url.origin !== "https://jseek.invalid") return null;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return null;
  }
}

export function withAuthReturnPath(authPath: string, returnPath: string | null): string {
  if (!returnPath) return authPath;
  const params = new URLSearchParams({ next: returnPath });
  return `${authPath}?${params.toString()}`;
}

/** Keep the requested surface while honoring the user's stored UI locale. */
export function localizeAuthReturnPath(returnPath: string, locale: string): string {
  return LOCALE_PREFIX.test(returnPath)
    ? returnPath.replace(LOCALE_PREFIX, `/${locale}`)
    : returnPath;
}
