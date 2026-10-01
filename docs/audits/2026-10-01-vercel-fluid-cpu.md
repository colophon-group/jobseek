# October 1 Fluid CPU verification

**FAIL (INCOMPLETE EVIDENCE).** The exact production window **September 30
20:00–October 1 08:00 UTC** used **250 billed CPU seconds** (223 Functions +
27 middleware). All 45 displayed Function route rows sum to **171.513 visible
CPU seconds across 1,315 invocations**, above the 138.5-second release budget.
Company pages remain the largest visible cost: **86s / 352 calls**, followed
by public search at **35s / 421 calls**. No new application fix or production
deployment was made by this verification.

## Identity and scope

- Live deployment: `dpl_FiG83oGn5uQfAZjBV6kjSjWLu6sJ`.
- SHA: `dae3f51fa96804c0a8cca0c4e1a3f52bc289f386` (#10206).
- Ready: September 30 **18:21:50.332 UTC**; promotion verified
  **18:23:19.5684284 UTC** by successful
  [owned workflow 36757707441](https://github.com/colophon-group/jobseek/actions/runs/36757707441).
- Both staged company cache guards recorded **zero Function invocations**.
- Fresh main `8d5e17e94f949c8a5e35c53878c988b7d3ad4c9a` has no cumulative
  web-relevant delta from live. The deployed SHA is latest web-relevant main.
- Active WAF remains enabled **v7**, updated August 30
  **18:27:57.013 UTC**. Version 8 is an unpublished draft.

The absolute window was fixed at **08:02:49.659757 UTC**, and retained natural
requests were captured before synthetic functionality probes. Both window
boundaries are after current promotion and the last active WAF publication.
The separate **<300 billed seconds / complete 24 hours** target cannot yet be
evaluated for this deployment: its earliest conservative full-day end is
**October 1 18:24 UTC**, unless another deployment or WAF publication resets it.
This report does not extrapolate a daily total from the half-day measurement.

## CPU, cache and errors

The September 23 **04:05–16:05 UTC** baseline was re-read alongside the after
window, with the `jobseek-web` project filter and Type grouping. It remains
**296 billed seconds = 235 Functions + 61 middleware**. Current billing is
**250s = 223 + 27**, a **15.5%** total decrease; middleware is **55.7%** lower.
These are different traffic windows, so the deltas are not causal savings
attributable to one patch. The gate's separate historical visible-CPU baseline
is **277s**, not the September 23 billed total.

| Exhaustive visible route group | CPU | Invocations | CPU budget |
|---|---:|---:|---:|
| Company OG | 0s | 0 | 24s |
| Company pages | 86s | 352 | 60s |
| Watchlist UUID and owner-only legacy routes | 11.380s | 179 | 25s |
| Explore | 10s | 91 | 10s |
| Other | 64.133s | 693 | 19.5s |
| **Total** | **171.513s** | **1,315** | **138.5s** |

The Functions table was Production, page size 50, **page 1 of 1**, with Next
disabled. Every row and displayed CPU label is retained in
[measurement.json](2026-10-01-vercel-fluid-cpu/measurement.json). The sum
reconciles exactly across the route groups. Billing and route labels are
rounded dashboard values: the three decimal places arise from summing those
labels, not from a raw CPU export. **Billed Functions minus visible route CPU
is 51.487s**, still unexplained; neither total replaces the other. Middleware's
zero row in the Functions view does not negate its 27 billed seconds.

| Other metric | Observation | Gate |
|---|---:|---:|
| Active CPU P75 | 273ms | ≤308ms: pass |
| CPU throttle P75 | 13.7% | ≤7.6%: fail |
| Global errors | <0.1% | ≤0.5%: pass |
| Timeouts | 0% | ≤0.1%: pass |
| PPR shell hits / misses | 214 / 541 = 28.3% | ≥35%: fail |
| Typesense outbound | `6.2K` label; exact count unknown | incomplete |
| Upstash outbound | `2K` label; exact count unknown | incomplete |

The UUID watchlist route separately shows **0.6% errors** across 163 calls.
Its cause and exact count are not recoverable from this rounded label or the
retained natural sample. Current owner/shared/private live checks pass; that
does not erase the historical error signal. The evaluator receives **0.1% as
a conservative upper bound** for the global `<0.1%` label, explicitly
annotated in the input; it is not reported as an exact observed error rate.
PPR's overall dynamic count is 469 while every route's dynamic count is zero,
an unresolved dashboard inconsistency.

Compared with the September 27 clean window, company CPU fell **106s → 86s**
while company invocations fell **427 → 352**. Ratios of rounded labels are
approximately **248ms → 244ms per invocation**, with different route and
request mixes. This is insufficient to establish production savings from
#10147's facts-only server snapshot. Total visible CPU increased
**160.990s → 171.513s** while all-route invocations increased **1,042 → 1,315**.
Search increased **22s / 276 → 35s / 421**, with no search fix deployed.

## Natural traffic and interpretation

The authenticated dashboard request-log endpoint returned **45 unique
production request IDs**, all on the measured deployment, spanning only
**07:07:54.711–07:59:35.651 UTC**. Five-minute sliced reads from 07:15 through
08:00 added no IDs; those slices did not report more rows. The source is
observed working, with no established stable API or all-traffic/sampling
contract. `sourceIncludesAllTraffic` remains **false**, and sampling remains
**null**. This short retained sample cannot represent the whole 12 hours.

The company subset has **13 non-curl company/locale keys** and **13
PRERENDER/resuming requests: five GETs and eight POSTs**. Each has a background
Function event, a partial-prerender Function event, and a static PPR event
with **TTL 86400s / age 0**. No static-only company HIT, `stale_time`, or
`delete_tag` appears in this sample. None of the company requests has an
application log message, function-crash flag, or non-200 status. These facts
do not establish why each request resumed, identify an invalidating action,
show all resumes are first GETs, or prove POST abuse. Event wall duration is
not billed CPU and is not used for attribution.

Across all 45 requests, user agents claim PetalBot (8), Amazonbot (1), bingbot
(1), and ClaudeBot (1). These are **11 user-agent claims**, not independently
verified bot identities. There are only 13 company/locale keys against the
20-key gate. Sanitized request details are in
[natural-traffic.json](2026-10-01-vercel-fluid-cpu/natural-traffic.json).
Raw client/session/query data, full user agents, and private fixture IDs stay
outside Git in the private durable evidence store.

The existing static-HIT adapter defect remains fixed (#9930 stays closed),
and observed daily TTL agrees with the #10038 lifetime fix. Current evidence
still points to generation/resume traffic and public search as the main
remaining visible costs. It does **not** justify broad warming, blocking
legitimate clients, or narrowing the shared taxonomy invalidation tag without
a reliable changed-company publication/retry contract. No unmeasured patch
was substituted for the missing attribution.

## Functionality and evidence limits

All current functionality gates passed after the measurement window: home,
Explore initial results and Remote-filter results, company facts/jobs,
complete direct **1200×630 PNG**, legacy OG **308**, actual owned and existing
unlisted shared UUID results, anonymous and cross-owner private denial after
hydration, legacy anonymous/cross-owner **404**, owner legacy **307**, and
the existing authenticated session/sign-in flow. Scanner probing still
returns **403**. No sharing settings were changed and retired public access
was not restored. Individual redirect/status receipts were confirmed in
post-window request logs, kept separate from natural traffic.

The initial direct-image request with Python's default User-Agent returned
403; a standard curl User-Agent returned the complete PNG. Both observations
are retained in the private probe evidence; no security settings changed.
See [functionality.json](2026-10-01-vercel-fluid-cpu/functionality.json).

CLI was updated locally **61.1.0 → 62.1.0** under existing upgrade authority.
The known metrics paywall was not retried and no paid plan was enabled.
Rounded outbound labels remain null, baseline route data remains expired,
and complete-window natural traffic is unavailable. No other task's paused
archive was restarted. [Schema-v2 gate input](2026-10-01-vercel-fluid-cpu/gate-report.json)
and [evaluated result](2026-10-01-vercel-fluid-cpu/gate-result.md) preserve
these gaps and the failed CPU/cache/traffic checks.

The follow-up remains active. Remaining work is tracked by #9916 (company),
#9917 (middleware), #9918 (measurement and reconciliation), #10015 (evidence
gaps), and #10037 (search); this failed gate is also reported to #6616.
