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

import { lookup as dnsLookup } from "node:dns/promises";
import type { LookupAddress } from "node:dns";

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
 * @param host    Hostname (no port, no path).
 * @param pattern Allowlist pattern.
 * @returns true iff `host` is allowed by `pattern`.
 */
export function matchHostPattern(host: string, pattern: string): boolean {
  const h = host.toLowerCase();
  const p = pattern.toLowerCase();

  if (!p.startsWith("*.")) {
    return h === p;
  }

  // `*.example.com` => suffix `.example.com` and at least one label
  // before it. The bare apex `example.com` does NOT match.
  const suffix = p.slice(1); // ".example.com"
  if (!h.endsWith(suffix)) return false;
  const prefix = h.slice(0, h.length - suffix.length);
  // prefix must be one-or-more non-empty labels (i.e. not empty and not
  // contain a leading dot).
  if (prefix.length === 0) return false;
  if (prefix.startsWith(".")) return false;
  return true;
}

/**
 * Internal helper: does `host` match ANY allowlist pattern?
 */
function isHostAllowed(host: string): boolean {
  for (const pattern of HOST_ALLOWLIST) {
    if (matchHostPattern(host, pattern)) return true;
  }
  return false;
}

/**
 * Internal: parse a dotted-quad IPv4 string into 4 octets, or null.
 */
function parseIPv4(ip: string): [number, number, number, number] | null {
  const parts = ip.split(".");
  if (parts.length !== 4) return null;
  const out: number[] = [];
  for (const part of parts) {
    if (!/^\d{1,3}$/.test(part)) return null;
    const n = Number(part);
    if (n < 0 || n > 255) return null;
    out.push(n);
  }
  return out as [number, number, number, number];
}

/**
 * Classify an IPv4 address as private/reserved.
 */
function isPrivateIPv4(ip: string): boolean {
  const parts = parseIPv4(ip);
  if (!parts) return false;
  const [a, b] = parts;

  // 0.0.0.0/8 unspecified / current network
  if (a === 0) return true;
  // 10.0.0.0/8 RFC1918
  if (a === 10) return true;
  // 100.64.0.0/10 CGNAT
  if (a === 100 && b >= 64 && b <= 127) return true;
  // 127.0.0.0/8 loopback
  if (a === 127) return true;
  // 169.254.0.0/16 link-local (includes metadata 169.254.169.254)
  if (a === 169 && b === 254) return true;
  // 172.16.0.0/12 RFC1918
  if (a === 172 && b >= 16 && b <= 31) return true;
  // 192.0.0.0/24 IETF protocol assignments
  if (a === 192 && b === 0 && parts[2] === 0) return true;
  // 192.0.2.0/24 TEST-NET-1
  if (a === 192 && b === 0 && parts[2] === 2) return true;
  // 192.168.0.0/16 RFC1918
  if (a === 192 && b === 168) return true;
  // 198.18.0.0/15 benchmarking
  if (a === 198 && (b === 18 || b === 19)) return true;
  // 198.51.100.0/24 TEST-NET-2
  if (a === 198 && b === 51 && parts[2] === 100) return true;
  // 203.0.113.0/24 TEST-NET-3
  if (a === 203 && b === 0 && parts[2] === 113) return true;
  // 224.0.0.0/4 multicast
  if (a >= 224 && a <= 239) return true;
  // 240.0.0.0/4 reserved (and 255.255.255.255 broadcast)
  if (a >= 240) return true;

  return false;
}

/**
 * Normalise an IPv6 string (lowercased, expanded `::`) into eight 16-bit
 * groups. Returns null on parse failure.
 */
function parseIPv6(ip: string): number[] | null {
  const lower = ip.toLowerCase();
  // IPv4-mapped form: ::ffff:1.2.3.4 — caller handles separately.
  if (lower.includes(".")) return null;

  const parts = lower.split("::");
  if (parts.length > 2) return null;

  const head = parts[0] === "" ? [] : parts[0].split(":");
  const tail = parts.length === 2 ? (parts[1] === "" ? [] : parts[1].split(":")) : [];

  if (parts.length === 1 && head.length !== 8) return null;
  if (parts.length === 2 && head.length + tail.length > 7) return null;

  const fillCount = 8 - head.length - tail.length;
  const groups: number[] = [];
  for (const g of head) {
    if (!/^[0-9a-f]{1,4}$/.test(g)) return null;
    groups.push(parseInt(g, 16));
  }
  for (let i = 0; i < fillCount; i++) groups.push(0);
  for (const g of tail) {
    if (!/^[0-9a-f]{1,4}$/.test(g)) return null;
    groups.push(parseInt(g, 16));
  }
  if (groups.length !== 8) return null;
  return groups;
}

/**
 * Classify an IPv6 address as private/reserved.
 */
function isPrivateIPv6(ip: string): boolean {
  const lower = ip.toLowerCase();

  // Unspecified `::`
  if (lower === "::" || lower === "0:0:0:0:0:0:0:0") return true;
  // Loopback `::1`
  if (lower === "::1" || lower === "0:0:0:0:0:0:0:1") return true;

  // IPv4-mapped IPv6: `::ffff:a.b.c.d` — re-classify the embedded v4.
  const v4MappedMatch = lower.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/);
  if (v4MappedMatch) {
    return isPrivateIPv4(v4MappedMatch[1]);
  }

  const groups = parseIPv6(lower);
  if (!groups) return false;

  const first = groups[0];

  // fe80::/10 link-local (first 10 bits = 1111111010)
  if ((first & 0xffc0) === 0xfe80) return true;
  // fc00::/7 ULA (first 7 bits = 1111110)
  if ((first & 0xfe00) === 0xfc00) return true;
  // ff00::/8 multicast
  if ((first & 0xff00) === 0xff00) return true;
  // ::/8 reserved (covers IPv4-compat and other legacy)
  if ((first & 0xff00) === 0x0000 && groups.slice(1, 7).every((g) => g === 0)) {
    // ::1 already handled; ::ffff: handled; bare :: handled. Any other
    // ::xxxx is suspicious — treat as private to fail closed.
    return true;
  }

  return false;
}

/**
 * Classify an IP address as public or private/reserved.
 *
 * Treats as PRIVATE (returns true):
 *   - Loopback           (`127.0.0.0/8`, `::1`)
 *   - RFC1918            (`10/8`, `172.16/12`, `192.168/16`)
 *   - CGNAT              (`100.64/10`)
 *   - Link-local         (`169.254.0.0/16`, `fe80::/10`)
 *   - Metadata services  (`169.254.169.254`)
 *   - IPv6 ULA           (`fc00::/7`)
 *   - IPv4-mapped IPv6   (`::ffff:<v4>` — re-classify the embedded v4)
 *   - Unspecified        (`0.0.0.0`, `::`)
 *   - Multicast / broadcast / IETF-reserved ranges
 *
 * Anything else is public.
 */
export function isPrivateIp(ip: string): boolean {
  if (parseIPv4(ip)) return isPrivateIPv4(ip);
  return isPrivateIPv6(ip);
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
  // Phase 0: parse + scheme check.
  let url: URL;
  try {
    url = new URL(input);
  } catch {
    return { ok: false, error: "url_invalid" };
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    return { ok: false, error: "url_invalid" };
  }
  if (!url.hostname) {
    return { ok: false, error: "url_invalid" };
  }

  const host = url.hostname.toLowerCase();

  // Phase 1: allowlist match. Performs NO DNS lookup on miss.
  if (!isHostAllowed(host)) {
    return { ok: false, error: "url_not_allowed" };
  }

  // Phase 2: DNS resolution + IP filter.
  let resolved: LookupAddress;
  try {
    // `verbatim: false` asks Node to honour the system's address-family
    // preference; it does NOT relax filtering. We capture both the
    // address and the family so safeFetch can pin the connection.
    resolved = await dnsLookup(host, { verbatim: false });
  } catch {
    return { ok: false, error: "url_dns_failed" };
  }

  const family = resolved.family === 6 ? 6 : 4;
  if (isPrivateIp(resolved.address)) {
    return { ok: false, error: "url_resolves_to_private" };
  }

  return { ok: true, url, resolvedIp: resolved.address, family };
}

/**
 * Error thrown by `safeFetch` when validation fails.
 */
export class SafeFetchError extends Error {
  public readonly code: ValidateUrlErrCode;
  constructor(code: ValidateUrlErrCode, message?: string) {
    super(message ?? code);
    this.name = "SafeFetchError";
    this.code = code;
  }
}

/**
 * Fetch a URL after validation, with DNS-rebinding protection.
 *
 * Resolves the hostname *once* via `validateUrl` and captures the IP.
 * The outbound request is then issued against a URL whose hostname has
 * been rewritten to that captured IP, with the original Host header
 * preserved on the request so virtual-hosted servers route correctly.
 *
 * Why this works as rebinding mitigation: by substituting the IP into
 * the URL we prevent any further DNS resolution. The connect call has
 * no opportunity to consult the resolver again, so an attacker who
 * controls the hostname's DNS cannot redirect us to a private IP after
 * the validation check has passed.
 *
 * Trade-off: this approach breaks TLS hostname verification (the
 * server's certificate is for the original hostname, not the IP). For
 * the M0 demo all probe targets are public ATS providers; if/when this
 * is reused for richer transport, swap to a `node:https` Agent with a
 * custom `lookup` AND `servername` so SNI continues to use the original
 * hostname while the connection still routes to the captured IP.
 *
 * Returns whatever `fetch` produces. If validation fails, throws
 * `SafeFetchError` with `.code` matching `ValidateUrlErrCode`.
 *
 * Callers at route boundaries should still invoke `validateUrl` first
 * to surface a structured error to the agent; `safeFetch` is the
 * defence-in-depth wrapper for the actual outbound call.
 */
export async function safeFetch(
  input: string,
  init?: RequestInit,
): Promise<Response> {
  const v = await validateUrl(input);
  if (!v.ok) {
    throw new SafeFetchError(v.error);
  }

  // Defence in depth: re-check the captured IP. If anything has stuffed
  // a private IP into our captured value, fail closed before opening a
  // socket.
  if (isPrivateIp(v.resolvedIp)) {
    throw new SafeFetchError("url_resolves_to_private");
  }

  // Pin the connection to the captured IP by substituting it into the
  // URL hostname. Bracket IPv6 literals.
  const ipUrl = new URL(v.url.toString());
  ipUrl.hostname = v.family === 6 ? `[${v.resolvedIp}]` : v.resolvedIp;

  // Preserve the original Host header so virtual-hosted servers still
  // route the request to the right vhost.
  const headers = new Headers(init?.headers);
  if (!headers.has("host")) {
    headers.set("host", v.url.host);
  }

  return fetch(ipUrl, { ...init, headers });
}
