type AuthOriginEnvironment = Partial<Pick<NodeJS.ProcessEnv, "TRUSTED_ORIGINS" | "VERCEL" | "VERCEL_ENV" | "VERCEL_URL">>;

/**
 * The production artifact is tested at its exact Vercel deployment URL before
 * promotion. Trust only that platform-provided hostname, never request headers,
 * arbitrary preview deployments, or a wildcard covering other Vercel tenants.
 */
export function trustedAuthOrigins(environment: AuthOriginEnvironment): string[] {
  const configured = (environment.TRUSTED_ORIGINS ?? "").split(",").filter(Boolean);
  if (environment.VERCEL !== "1" || environment.VERCEL_ENV !== "production") return configured;
  const hostname = environment.VERCEL_URL;
  // VERCEL_URL is a scheme-free, single-label deployment hostname. Reject all
  // URL syntax, suffix tricks, ports, extra subdomains, whitespace and IPs.
  if (!hostname || hostname !== hostname.trim() || !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.vercel\.app$/i.test(hostname)) return configured;
  return [...new Set([...configured, `https://${hostname.toLowerCase()}`])];
}
