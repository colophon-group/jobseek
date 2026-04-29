/**
 * SSRF allowlist + DNS-rebinding-aware fetcher for agent-supplied URLs.
 *
 * Used by every Murmur subcommand route in `apps/web/app/api/**` that
 * accepts a URL field (`board_url`, `sample_url`, ...). Two phases:
 *
 *   1. Host-pattern allowlist (synchronous, no DNS).
 *      Hard-coded list. Updates require a code change; no env-var
 *      override.
 *
 *   2. DNS resolution + IP filter (rebinding-aware).
 *      Resolve once; reject if the address is private, loopback,
 *      link-local, an IANA-reserved cloud-metadata IP, or an IPv6 ULA.
 *      `safeFetch` opens the connection to the *captured* IP so a second
 *      DNS lookup cannot redirect us to a private address.
 *
 * Allowlist scope: the initial set covers the demo's most common board
 * providers. Expand patterns as J5/#2759's routes encounter legitimate
 * rejections in real demo runs. The full reference list of board hosts
 * is at `apps/crawler/data/boards.csv`.
 *
 * Caller boundary: only files under `apps/web/app/api/**` (route
 * boundaries) and this module's own test file may import from here. The
 * `scripts/grep-validateurl-boundary.sh` gate enforces this.
 *
 * @see colophon-group/jobseek#2758
 * @see Murmur DESIGN.md §3.6 (Publisher SSRF defense), §4.2 (Probe SSRF defense)
 */

/**
 * Host-pattern allowlist. Each entry is either:
 *   - A literal host (e.g. `boards.greenhouse.io`) — exact match only.
 *   - A leading-wildcard pattern (e.g. `*.greenhouse.io`) — matches any
 *     subdomain at any depth, but NOT the bare apex (`greenhouse.io`).
 *
 * To add a host: edit this array. Code review is the gate; no runtime
 * override exists. Reference list of all known board hosts in use today:
 * `apps/crawler/data/boards.csv` (the `board_url` column).
 *
 * Expand as J5 routes encounter legitimate rejections in real demo runs.
 */
export const HOST_ALLOWLIST: readonly string[] = [
  // Greenhouse (most popular ATS in boards.csv)
  "*.greenhouse.io",
  "boards.greenhouse.io",
  "job-boards.greenhouse.io",
  // Lever
  "*.lever.co",
  "jobs.lever.co",
  // Workday
  "*.myworkdayjobs.com",
];

export type ValidateUrlOk = {
  ok: true;
  url: URL;
  resolvedIp: string;
  family: 4 | 6;
};

export type ValidateUrlErrCode =
  | "url_invalid"
  | "url_not_allowed"
  | "url_dns_failed"
  | "url_resolves_to_private";

export type ValidateUrlErr = {
  ok: false;
  error: ValidateUrlErrCode;
};

export type ValidateUrlResult = ValidateUrlOk | ValidateUrlErr;

/**
 * Match a hostname against a single allowlist pattern.
 *
 * Patterns:
 *   - `host.example.com` — exact match (case-insensitive).
 *   - `*.example.com` — any strict subdomain (one or more labels).
 *
 * No support for mid-string wildcards or path patterns; we deliberately
 * keep the matcher tiny so its behaviour is auditable in one glance.
 *
 * @param host    Lowercased hostname (no port, no path).
 * @param pattern Allowlist pattern.
 * @returns true iff `host` is allowed by `pattern`.
 */
export function matchHostPattern(host: string, pattern: string): boolean {
  void host;
  void pattern;
  throw new Error("not implemented");
}

/**
 * Validate a URL against the SSRF allowlist + IP filter.
 *
 * Performs phase 1 (allowlist) synchronously and phase 2 (DNS) only if
 * phase 1 passes. Returns the resolved IP so the caller can route via a
 * rebinding-aware fetcher.
 *
 * Never throws — all error states are encoded in the return shape.
 *
 * Error codes:
 *   - `url_invalid`              — input was not a parseable absolute URL with `http(s):` scheme.
 *   - `url_not_allowed`          — host did not match any allowlist pattern. NO DNS LOOKUP IS PERFORMED.
 *   - `url_dns_failed`           — DNS resolution failed (NXDOMAIN, timeout, no A/AAAA record).
 *   - `url_resolves_to_private`  — resolved address is private, loopback, link-local, metadata, or ULA.
 */
export async function validateUrl(input: string): Promise<ValidateUrlResult> {
  void input;
  throw new Error("not implemented");
}

/**
 * Fetch a URL after validation, with DNS-rebinding protection.
 *
 * Resolves once via `validateUrl`, captures the IP, and opens the TCP/TLS
 * connection to that captured IP — supplying the original hostname as the
 * `Host` header and TLS SNI so the server's certificate still validates.
 * If the agent's libc were to re-resolve mid-flight to a private address,
 * we don't see it: the connection has already been pinned.
 *
 * For non-https schemes the function still works (uses HttpAgent), but
 * production callers should prefer https.
 *
 * Returns the underlying `Response`. If validation fails, throws an Error
 * whose `.code` matches `ValidateUrlErrCode`. Callers at route boundaries
 * are expected to invoke `validateUrl` first and surface the structured
 * error to the agent; `safeFetch` is a defence-in-depth wrapper for the
 * actual outbound call.
 */
export async function safeFetch(
  input: string,
  init?: RequestInit,
): Promise<Response> {
  void input;
  void init;
  throw new Error("not implemented");
}

/**
 * Internal: classify an IP address as public or private/reserved.
 * Exported only for unit tests.
 *
 * Treats as PRIVATE (returns true):
 *   - Loopback           (`127.0.0.0/8`, `::1`)
 *   - RFC1918            (`10/8`, `172.16/12`, `192.168/16`)
 *   - Link-local         (`169.254.0.0/16`, `fe80::/10`)
 *   - Metadata services  (`169.254.169.254`, `fd00:ec2::254`)
 *   - IPv6 ULA           (`fc00::/7`)
 *   - IPv4-mapped IPv6   (`::ffff:<v4>` — re-classify the embedded v4)
 *   - Unspecified        (`0.0.0.0`, `::`)
 *   - Multicast / broadcast / reserved ranges
 *
 * Anything else is public.
 */
export function isPrivateIp(ip: string): boolean {
  void ip;
  throw new Error("not implemented");
}

