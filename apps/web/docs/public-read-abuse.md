# Public read abuse controls

Explore, company detail, public-watchlist discovery, and public-watchlist
detail all expose pagination through Next.js Server Actions. Action IDs are
routing metadata embedded in the client bundle; they are not authorization
tokens and must never be treated as a security boundary.

## Request controls

The controls are layered so a deployment or provider failure does not remove
the whole boundary:

1. **Vercel WAF** — production `POST` requests with a `Next-Action` header on
   the four public read route shapes are rate-counted by authoritative IP.
   Roll a new threshold out with the exceeded action set to `log`, review its
   Firewall Traffic matches, then change the exceeded action to `rate_limit`
   (HTTP 429). Match the header's existence, not a deployment-specific action
   hash.
2. **Next.js Proxy** — the same route/header shapes use shared Upstash sliding
   windows of 30 requests/minute and 300 requests/hour per authoritative IP.
   A rejected request receives a non-cacheable 429 before the page action
   runs. Redis transport failures fail open and emit a sanitized
   `public_read.rate_limit_unavailable` event.
3. **Action validation** — replayed pagination is limited to `limit <= 100`
   and `offset <= 5000`; the application UI uses pages of 10-20. Server Action
   bodies are limited to 128 KB instead of Next's 1 MB default.
4. **Anonymous result caps** — existing surface-specific result caps remain a
   product boundary, but they are not a substitute for request rate limits.

The Proxy key comes from `x-real-ip`, falling back to the final
`x-forwarded-for` hop that Vercel appends. Never use the first forwarded hop;
the caller can spoof it. Logs contain only a short SHA-256 client reference,
not the raw IP or Redis error object.

Browser-direct Typesense search is a separate ingress path. Its search-only
key is public by design, so keep the Cloudflare per-IP rate limit on
`typesense.colophon-group.org` enabled and review it alongside Vercel traffic.

## Daily anomaly review

Review at least the last 24 hours when retention permits, plus a narrow recent
window for active abuse. Compare each dimension with its usual baseline:

- allowed, denied, challenged, and rate-limited requests;
- top IP and ASN concentrations;
- route, method, status, and cache-bypass concentrations;
- user-agent and JA4 concentrations;
- Server Action paths with clockwork cadence or fixed query fingerprints;
- `public_read.rate_limited` and `public_read.rate_limit_unavailable` events.

Investigate one client producing more than 300 public read actions in five
minutes, more than half of a route's requests, or a sustained non-human
cadence. A browser user-agent alone is not evidence of a browser; corroborate
it with hosting ASN, cadence, path concentration, and JA4/IP concentration.

For active abuse, stage the narrowest hostname-scoped IP block and inspect the
Firewall draft. Publish only after verifying the exact IP/host and the full
draft. Do not block a shared Chrome JA4 or user-agent globally. Keep new
behavioral rules in log mode until their matches exclude ordinary users,
verified search crawlers, and internal automation.
