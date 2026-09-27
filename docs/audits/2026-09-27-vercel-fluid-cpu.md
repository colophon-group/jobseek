# Fluid CPU verification — September 27, 2026

The first completed window after #10038 still **fails** the CPU gate, with
incomplete telemetry. Billed CPU fell to **234 seconds** from the re-read
September 23 baseline of 296 seconds (20.9% lower), and from the preceding
September 26 window's 342 seconds (31.6% lower). Traffic changed materially;
these differences are observed totals, not causal savings estimates.

The daily company-shell lifetime is now confirmed in production. PPR shell
hit rate rose from 25.9% to **55.2%**. Company pages remain the largest visible
CPU group: **106 seconds, 65.8% of visible route CPU**.

## Deployment and absolute window

- Deployment: `dpl_Acz8SwgKcvu5XjeFgWhzwA7XeEyz`.
- SHA: `e65fb972e4fbb349d10ed66dc9418b1d271f52ca` (#10038).
- Ready: `2026-09-26T13:06:20.750Z`.
- Promotion verified: `2026-09-26T13:07:38.4192087Z` by
  [owned workflow 36243899839](https://github.com/colophon-group/jobseek/actions/runs/36243899839).
- Both staged company HIT guards invoked zero Functions: English Aircall and
  French HelloFresh. The workflow completed successfully.
- Fresh main and live production matched at the September 27 check.
- Active WAF re-read: enabled, version 7, updated
  `2026-08-30T18:27:57.013Z`. Version 8 remains an unpublished draft.
- Fixed window: **September 26 23:10–September 27 11:10 UTC**, exactly 12 hours.
  Natural request logs were captured before synthetic functionality probes.

## Measured CPU and cache

Baseline billing was re-read alongside after billing. All after billing
belonged to `jobseek-web`.

| Metric | September 26 prior window | September 27 window | Gate |
|---|---:|---:|---:|
| Billed Functions CPU | 316s | 214s | Separate from visible CPU |
| Billed middleware CPU | 26s | 20s | Separate from visible CPU |
| Billed total CPU | 342s | 234s | Separate from visible CPU |
| Visible route CPU | 247.515s | 160.990s | ≤138.5s — fail |
| Invocations, exhaustive route sum | 2,037 | 1,042 | Traffic changed |
| CPU P75 | 264ms | 310ms | ≤308ms — fail |
| CPU throttle P75 | 8.5% | 10.1% | ≤7.6% — fail |
| Errors / timeouts | 0% / 0% | 0% / 0% | pass |
| PPR hits / misses | 166 / 475 | 391 / 317 | 55.2% — pass |

All **23 route rows**, including the zero middleware row, were captured from
page 1 of 1 with page size 50 and Next disabled. Values retain the dashboard's
rounding; 160.990 seconds is the sum of displayed route CPU, not a higher
precision billing measurement. Groups reconcile exactly to that sum.

| Group | Invocations | Visible CPU | Budget |
|---|---:|---:|---:|
| Company OG | 0 | 0s | 24s |
| Company pages | 427 | 106s | 60s — fail |
| Watchlist routes, including legacy | 33 | 2.54s | 25s |
| Explore | 47 | 11s | 10s — fail |
| Other | 535 | 41.45s | 19.5s — fail |

The largest other routes are `/api/v1/search` (22s / 276),
`/api/typesense-key` (9s / 95), and `/mcp` (4.33s / 79). Search declined from
55s / 766 in the prior window, with no search fix deployed; #10037 remains an
investigation. Overall invocations fell 48.8%, while visible CPU fell 35.0%.
Neither comparison proves an improvement in CPU per equivalent request.

Billed Functions CPU exceeds visible route CPU by **53.010 seconds**. This
unresolved difference stays separate under #9918. Historical baseline route
data has expired from Hobby retention; a dashboard substitution of Last hour
is not baseline evidence.

PPR reports 312 dynamic invocations, while every route's dynamic column is
zero. That breakdown is inconsistent; no route attribution is inferred from
it. External labels are Typesense `5K`, Upstash `1.5K`, assets `325`,
`jseek.co` `19`, Google OAuth `1`, and Vercel Data Cache `1`. The first two
remain **null** in the gate because their exact counts are unavailable. CLI
60.1.3 still returned `payment_required` during the September 26 investigation.
The requester declined a paid upgrade; no plan or telemetry purchase was made.

## What the retained natural requests establish

The request-log endpoint returned **50 unique requests**, all from the live
deployment, from **10:19:50.086–11:08:59.271 UTC**. A subsequent five-minute
sliced re-read added no IDs; the oldest slice returned
`ExceedsBillingLimitError`. This is retained partial evidence, not complete
12-hour coverage or a verified sampling contract.

Of 25 company requests:

- 12 GET HITs were static-only, with zero Function events.
- 12 PRERENDER/resuming requests (10 GET, 2 POST) each showed both
  `background_func` and `partial_prerender` serverless events, plus static.
- One GET was a `prerender_bypass` request to a literal `[slug]` path.
- No `stale_time` or `delete_tag` reason appeared in this short sample.
- Excluding curl leaves 17 distinct company/locale keys, below the 20-key
  natural-traffic gate. The complete retained sample includes one AhrefsBot
  user-agent claim. User-agent strings do not verify a bot's identity.

Static cache events now expose TTLs of **86,399–86,400 seconds**. A TSMG HIT
had cache age **67,967 seconds** (18.9 hours); HPE, Goldman Sachs, Cato Networks,
and Dentsu also had old static HITs. This directly verifies that the former
one-hour shell lifetime no longer forces those requests into Functions.
Securitas, Figma, Maersk, Lam Research, and Nerdy each had a resuming request
followed by a static HIT in the sample.

The remaining resume requests are consistent with generation of an uncached
shell, but these records do not prove each key's cache history or attribute
billed CPU to individual events. Two of the resumes were POSTs. Do not equate
all 12 with first-time GETs, revive the fixed HIT-resume defect (#9930), or
estimate their billed CPU from wall duration. #9916 remains the place to
investigate the remaining company cost. Broad prewarming would itself invoke
Functions; adding every company to the build also needs a measured build-cost
and deployment-size trial before adoption.

## Live behavior and decision

Fresh checks after the window passed: localized home; Explore initial results
and a Remote filter with updated results; Aircall facts and 77 active jobs;
valid direct R2 PNG at 1200×630; legacy OG 308 to the same image; sign-in and
an existing owner session; an owned watchlist with actual title and 1,183 jobs;
and an existing unlisted shared link with 126 jobs while logged out.
Private UUID links denied anonymous and cross-owner access after hydration.
Legacy anonymous/cross-owner paths returned 404; the owner's legacy path
returned 307 to their UUID. The scanner-shaped path returned 403.
No watchlist sharing or access policy was changed.

The schema-v2 result is **FAIL (INCOMPLETE EVIDENCE)**. Keep the verification
follow-up active. The deployed cache fix is supported by production cache
behavior, but the complete CPU reduction target is not achieved. Continue
measuring comparable windows and target #9916's remaining company rendering
and #10037's search cost. Do not treat partial logs, rounded outbound counts,
or the lower request volume as proof that the full gate passed.

Sanitized evidence: [measurement](2026-09-27-vercel-fluid-cpu/measurement.json),
[company request events](2026-09-27-vercel-fluid-cpu/company-requests.json),
[functionality](2026-09-27-vercel-fluid-cpu/functionality.json),
[gate input](2026-09-27-vercel-fluid-cpu/gate-report.json), and
[gate result](2026-09-27-vercel-fluid-cpu/gate-result.md).
Raw client/session/query data remains outside Git.
