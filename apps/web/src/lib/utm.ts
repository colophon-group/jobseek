/**
 * Append utm_source=jobseek to an external URL.
 * Preserves any existing query parameters.
 */
export function withUtmSource(url: string): string {
  try {
    const u = new URL(url);
    if (!u.searchParams.has("utm_source")) {
      u.searchParams.set("utm_source", "jobseek");
    }
    return u.toString();
  } catch {
    return url;
  }
}
