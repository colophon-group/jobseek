# Public read abuse controls

Explore, company detail, public-watchlist discovery, and public-watchlist
detail all expose pagination through Next.js Server Actions. Action IDs are
routing metadata embedded in the client bundle; they are not authorization
tokens and must never be treated as a security boundary.

## Request controls

The controls are layered so a deployment or provider failure does not remove
the whole boundary:

1. **Vercel WAF** — one Hobby-plan rate-limit rule protects both production
   `POST` requests with a `Next-Action` header on the four public read route
   shapes and production `GET /api/v1/*` requests on `jseek.co`. The rule uses
   one fixed 60 requests/minute budget keyed by authoritative IP and denies
   excess requests before CDN lookup. The API group requires the exact host,
   environment, method, and namespace; the Server Action groups match the
   header's existence, not a deployment-specific action hash. The shared
   budget is intentional because Hobby permits one rate-limit rule per
   project. The pre-existing API/MCP log rule supplied the observation stage
   before the API group was added.
2. **Next.js Proxy** — the same route/header shapes use shared Upstash sliding
   windows of 30 requests/minute and 300 requests/hour per authoritative IP.
   A rejected request receives a non-cacheable 429 before the page action
   runs. Redis transport failures fail open and emit a sanitized
   `public_read.rate_limit_unavailable` event.
3. **Public REST origin** — every `/api/v1/*` cache miss still uses the
   existing Upstash sliding window of 30 requests/minute per authoritative IP.
   Cache hits do not invoke the route or Upstash, but they remain inside the
   WAF's coarser 60/minute boundary. Contract errors, provider failures, and
   origin 429s are `no-store` and may include caller-specific rate-limit
   metadata. Successful deterministic GETs omit those headers and cache only
   at Vercel via `Vercel-CDN-Cache-Control`.
4. **Action validation** — replayed pagination is limited to `limit <= 100`
   and `offset <= 5000`; the application UI uses pages of 10-20. Server Action
   bodies are limited to 128 KB instead of Next's 1 MB default.
5. **Anonymous result caps** — existing surface-specific result caps remain a
   product boundary, but they are not a substitute for request rate limits.

The Proxy key comes from `x-real-ip`, falling back to the final
`x-forwarded-for` hop that Vercel appends. Never use the first forwarded hop;
the caller can spoof it. Logs contain only a short SHA-256 client reference,
not the raw IP or Redis error object.

Browser-direct Typesense search is a separate ingress path. Its search-only
key is public by design, so keep the Cloudflare per-IP rate limit on
`typesense.colophon-group.org` enabled and review it alongside Vercel traffic.

## Public API and MCP edge visibility

The topology from
[#8120](https://github.com/colophon-group/jobseek/issues/8120) is live. Vercel
CLI publication succeeded between `2026-08-28T09:37:58Z` and
`2026-08-28T09:38:06Z`; the CLI did not expose a narrower provider-side
timestamp. Post-publication inspection found three active, valid rules and no
unpublished draft. Always verify current state with
`vercel firewall rules list --json` and `vercel firewall diff --json`; this
document is not a substitute for a fresh inspection.

The final three-rule topology is:

1. `rule_log_ahrefs_company_og_30JOfZ` remains an enabled `deny` rule. Its
   first OR group is the existing exploit-scanner path regex; the following
   groups preserve the selected company-bot match and the exact obsolete
   Explore `Next-Action` match from #7189.
2. `rule_deny_public_read_server_action_bursts_SZRRK0`, displayed as
   **Deny public read and API bursts**, remains at position 2. Its first two
   OR groups preserve the public Server Action path/route conditions. Its
   third group requires `environment = production`, `host = jseek.co`,
   `method = GET`, and path regex `^/api/v1(?:/.*)?$`. All groups share the
   existing 60 requests per 60 seconds fixed window keyed by IP, with `deny`
   when exceeded.
3. `rule_log_public_api_and_mcp_traffic_w3Ii5m` is last and remains an enabled
   `log` rule. Each OR group requires `environment = production` and
   `host = jseek.co`; the paths are either
   `^/api/v1(?:/.*)?$` or exact `/mcp`.

The observation rule deliberately does not match preview deployments,
deployment hostnames, other domains, `/mcp.json`, or the broader `/api/*`
namespace. In particular, `/api/auth`, `/api/admin`, `/api/web`, and
`/api/typesense-key` remain outside the rule. Keep the action `log`; this
observation rule itself must not deny, challenge, bypass, or rate-limit public
API traffic. The preceding rate-limit rule is the enforcement boundary.

Open the live [API/MCP Firewall Traffic filter](https://vercel.com/viktor-shcherbakovs-projects/jobseek-web/firewall/traffic?filter=rule_log_public_api_and_mcp_traffic_w3Ii5m).
Firewall Traffic observes requests at the Vercel edge, so its count includes
CDN hits that never invoke an origin Function and requests denied by the WAF.
Application events and Redis counters measure origin executions instead. Use
the edge view for total ingress and enforcement; use origin metrics for
handler status, latency, consumer attribution, and cost. Do not add the two
counts together. A hosted MCP REST cache hit intentionally has no origin
consumer-attribution event because the response is identical for every
anonymous caller.

On Hobby, **Past Day is a rolling view, not durable history**. Record any
needed result before it ages out, and do not infer a 7-day or 30-day trend from
the filter. An unpublished draft does not inspect traffic and therefore cannot
produce matches.

Immediately before any future publication, confirm that the machine-readable
diff contains only the reviewed changes and that the two hostname-scoped IP
blocks are unchanged. Publication is user/operator-only and must run from the
linked repository root. Hobby permits three custom rules and one rate-limit
rule, so extend the combined rule instead of attempting to add a fourth rule:

```bash
vercel firewall diff --json
vercel firewall ip-blocks list --json
vercel firewall publish --yes
```

Any publication resets an active Vercel Fluid CPU measurement window; see
`docs/18-vercel-fluid-cpu.md`.

### Publication probes

The 2026-08-28 pre-publication baseline and post-publication verification both
returned edge-denied 403s for the scanner, bot, and obsolete-action probes.
Ordinary company, Explore, sign-in, REST, and MCP requests returned 200 after
publication; excluded `/api/typesense-key` remained 200, while `/mcp.json` and
`/api/v1foo` returned their normal unmitigated 404s. Reuse the bounded checks
below after future WAF changes. Structural inspection remains sufficient when
the combined rate-limit rule is unchanged. When its API group changes, wait
for a clean fixed window and run the bounded repeated-cache probe once.

```bash
# Existing deny branches: expect 403 and `x-vercel-mitigated: deny`.
curl -sS -D - -o /dev/null https://jseek.co/wp-admin
curl -sS -A 'AhrefsBot' -D - -o /dev/null \
  https://jseek.co/en/company/amazon
curl -sS -X POST \
  -H 'Next-Action: 7ffac6a500b0410a78dcf5f6a75ea0d2253b635222' \
  -D - -o /dev/null https://jseek.co/en/explore

# Ordinary traffic: expect 200 and no `x-vercel-mitigated` header.
curl -sS -D - -o /dev/null https://jseek.co/en/company/amazon
curl -sS -D - -o /dev/null \
  'https://jseek.co/en/explore?q=python&wm=remote'
curl -sS -D - -o /dev/null https://jseek.co/en/sign-in
curl -sS -D - -o /dev/null \
  'https://jseek.co/api/v1/search?q=python&limit=1'
curl -sS -X POST \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Mcp-Protocol-Version: 2025-06-18' \
  --data-binary '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"waf-verification","version":"1.0"}}}' \
  -D - -o /dev/null https://jseek.co/mcp

# The combined rate-limit rule must still be live and structurally exact.
vercel firewall rules inspect \
  rule_deny_public_read_server_action_bursts_SZRRK0 --json
vercel firewall diff --json

# Only after changing the API group: warm one deterministic URL, wait for a
# clean fixed window, then issue 61 identical GETs. At least one must be a WAF
# denial and ordinary requests must recover after the next window. Do not run
# this as routine monitoring.
for _ in $(seq 1 61); do
  curl -sS -o /dev/null -w '%{http_code}\n' \
    'https://jseek.co/api/v1/search?q=python&wm=remote&locale=en&lang=en'
done
```

Then select **Live** or **Past Hour** in the filtered Firewall Traffic view and
confirm both controlled REST and MCP requests appear under
`rule_log_public_api_and_mcp_traffic_w3Ii5m`. The log rule does not add a
mitigation response header. After publication and verification,
`vercel firewall diff --json` must report an empty `changes` array.

### Rollback constraints

To restore the pre-#8261 API enforcement while retaining the #8120 observation
topology, remove only the third `/api/v1` OR group from
`rule_deny_public_read_server_action_bursts_SZRRK0`, restore its prior name and
description, and publish after reviewing the full diff. The two Server Action
groups, rate-limit action, threshold, algorithm, key, and order must remain
unchanged.

To restore the pre-#8120 topology, first stage the reverse change: remove the
API/MCP log rule, remove only the scanner OR group from the retained deny rule,
and re-create the standalone scanner `deny` rule with its exact regex. Inspect
the complete draft, confirm the obsolete-action and company-bot groups and the
rate-limit rule are unchanged, then have a human operator publish it.

The obsolete-action branch has an additional safety constraint from #7189.
Before rolling production back to any deployment predating the persistent
`NEXT_SERVER_ACTIONS_ENCRYPTION_KEY` rollout, first remove that exact
`Next-Action` OR group from the consolidated rule and publish the firewall
change. In an emergency, disabling the consolidated rule also prevents the
old action from being denied, but temporarily removes its scanner and bot
protections. Restore the exact obsolete-action branch only after a deployment
with the persistent key is live again.

## Daily anomaly review

Review at least the last 24 hours when retention permits, plus a narrow recent
window for active abuse. Compare each dimension with its usual baseline:

- allowed, denied, challenged, and rate-limited requests;
- top IP and ASN concentrations;
- route, method, status, and cache-bypass concentrations;
- user-agent and JA4 concentrations;
- Server Action paths with clockwork cadence or fixed query fingerprints;
- `public_read.rate_limited` and `public_read.rate_limit_unavailable` events.

The Hobby project needs two complementary sources. The Firewall Traffic
dashboard exposes IP, ASN, country, user-agent, JA4, path, host, and firewall
action concentrations, but `vercel metrics` queries require Observability Plus.
The CLI request-log stream exposes method, response status, cache result,
deployment, source, and runtime messages, but omits IP, ASN, user-agent, and
JA4. Do not use either source alone for the daily review.

1. Open [Firewall Traffic](https://vercel.com/viktor-shcherbakovs-projects/jobseek-web/firewall/traffic?range=1d),
   select **Past Day**, and record the top IPs, ASNs, JA4 digests, user agents,
   request paths, hosts, and firewall outcomes. Then switch to **Past Hour** to
   identify attacks that began recently and would be diluted in the daily
   totals.
2. For each concentrated path, inspect a bounded recent runtime-log sample.
   The CLI `--query` option searches log messages; it does not filter request
   metadata. Pipe the JSONL through the repository analyzer to filter the exact
   `requestPath` locally:

   ```bash
   vercel logs --environment production --since 1h --limit 1000 --json \
     | pnpm --dir apps/web traffic:analyze --path /en/explore
   ```

   Aggregate method, status, cache/cache reason, timestamp span, source, and
   repeated error/action identifiers. Repeat `--path` to include a dynamic route
   template emitted as a separate log entry. A saturated 1,000-entry result is
   a sample, not a daily total; report its exact time span when deriving a
   request rate, and move `--since`/`--until` to inspect an older interval.
3. Review the unpublished firewall state before recommending any intervention:

   ```bash
   vercel firewall diff --json
   ```

   Distinguish staged controls from live controls in every report.

Investigate one client producing more than 300 public read actions in five
minutes, more than half of a route's requests, or a sustained non-human
cadence. A browser user-agent alone is not evidence of a browser; corroborate
it with hosting ASN, cadence, path concentration, and JA4/IP concentration.

For active abuse, stage the narrowest hostname-scoped IP block and inspect the
Firewall draft. Publish only after verifying the exact IP/host and the full
draft. Do not block a shared Chrome JA4 or user-agent globally. Keep new
behavioral rules in log mode until their matches exclude ordinary users,
verified search crawlers, and internal automation.
