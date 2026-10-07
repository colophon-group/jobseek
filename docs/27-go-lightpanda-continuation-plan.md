# Go and Lightpanda migration delivery plan

Updated 2026-10-07. Delivery remains the full migration; it is incomplete.

## Delivery objective

Replace every enabled crawler monitor/detail/browser profile with production Go
and self-hosted Lightpanda or proven Go HTTP/API routes. Replace mandatory Python
service, scheduling, deployment and maintenance consumers. Remove production
Python, Playwright, Chromium and runtime-only legacy assets after the supported
rollback window; preserve useful isolated offline Python and every enabled board.

Completion requires correct canonical fields and database effects, publisher
policy, description/R2 behavior, freshness and queue conservation; comparable
whole-service CPU/RAM/density/attributable cost; and a supported cold reversal
with its observation window. A merged candidate or synthetic benchmark does
not complete migration.

## Latest checkpoint — 2026-10-07

Release **0.13.963** merged in [PR #10349](https://github.com/colophon-group/jobseek/pull/10349)
at `3905501bcf9b769422f72aefa167388c17d20b2c`. Its 107 compiled profiles add
Manatal, HRMOS, Recruiterbox and jobs.ch/jobup: 28 enabled monitor configurations.
The final reviewed head passed full worker and queue race suites in 499.264 and
337.910 seconds, required CI, installed-image parity, both Linux architecture
pilots and Crawler Deploy Gate before the exact-head merge.

Original [crawler deployment 37624980250](https://github.com/colophon-group/jobseek/actions/runs/37624980250)
completed including promotion on its supported retry, using the original
immutable image pair. [Renderer deployment 37627611206](https://github.com/colophon-group/jobseek/actions/runs/37627611206)
also succeeded at the merged source with **Lightpanda 1.0.0**. The official release
list was rechecked October 7; 1.0.0 remains the latest stable release.

The prior source 962 supported retirement timed out at 12:19 UTC and contained
all writers. The reviewed 963 fix materializes historical membership once;
the same production ownership decision completed in 1.281 seconds instead of
timing out after 10.224 seconds. The unchanged installed wrapper successfully
retired the retained 962 identity with the verified immutable 963 administrator,
restored the complete old stack and passed readiness/restart arming. Original
B0 rollback and selector clear then succeeded. Independent cold 214 readback
passed at 13:19 UTC; no receipts, ownership projections, claim tokens, current
fences or orphan historical receipts remained. Historical audit evidence was
preserved. The original 963 deployment had refused promotion at the retained
receipt guard; retry occurred only after this supported reversal.

Independent deployed 963 cold 214 readback passed at 13:26 UTC. Original B0
activation/readback passed at 215, with healthy endpoints and real render commits.
Fresh promoted-source canonical/cache and actual scheduled posting routes admit
**7,240 monitors**, **2,616 detail boards** and **1,462,276 scheduled postings**.
Zero monitor configuration/cache mismatches remain. 40 cached detail
incompatibilities and one actual Eightfold route remain with their legacy owner.
The three exclusive B0 boards remain outside ordinary ownership. 642 enabled
monitors still need replacement contracts.

Original ordinary staging passed at 215 with plan
`f5e7ffe7f51db1175228aee5f7cab022a29fcfa852d7a1880552199d847bfaf3`
and projection `908296bf1504ad0f751f61432cd5839b4bb7e0bd`.
**Ordinary activation and independent serving verification passed.** The original
observation was interrupted after SQL ownership committed. Fresh host/SQL reads
showed no live operation, the exact pending receipt and the active intended plan.
The supported same-plan retry restored all ten services and restart policies.
The 17:04 UTC observation verifies eight health endpoints and 1,012 monitor /
190 detail completions across 32 profiles. A read-only runtime inspection observed one native
container restart at 16:21 UTC; its cause remains unproved. New-provider completions remain pending. Queue claim errors and
unacknowledged attempts require conservation/freshness review; health alone does
not prove them. See the [source 963 rollout](evidence/go-native-source963-rollout-2026-10-07.json).

Source 962's 11:45 UTC observation remains historical evidence: 1,401 monitor and
437 detail completions across 23 profiles, 29 canonical samples, eight healthy
endpoints and no processing diagnostics. See the
[source 962 rollout](evidence/go-native-source962-rollout-2026-10-07.json) and
[retirement correction](evidence/go-native-retirement-eligibility-2026-10-07.json).
Every-profile freshness, whole-service resources/cost and complete Python
retirement remain unproved.

## Continue delivery

1. Continue serving observation against source 963, its exact fresh plan,
   projection, image pair, B0 receipt and epoch 215. Observe real scheduled monitor
   and detail completions, canonical fields/descriptions, publisher outcomes,
   SQL/Redis deadlines, full-stack health and freshness. Preserve pending
   identity and containment on failure; use existing supported recovery.
2. Deliver the large shared browser paths together: DOM 239, API sniffer 149 and
   Inline 8 in the fresh remaining census. Initial API replay options screen 33
   configurations among 69 explicitly browser-backed API configurations.
   The API browser profile now includes private capture/credential refresh,
   single-target browser fetch, bounded trusted HTTP fallback, Python's browser
   50-page and HTTP 200-page defaults, mTLS service/client and native persistence.
   Full worker and queue race suites passed in 548.462 and 342.580 seconds at
   `cc5e3275bd291bdb84beb97e5831c4096af04eaa`. A Docker-context COPY correction
   at `0b64c8b047a45a504dc1a21b32249b5243eae759` passed actual Lightpanda 1.0.0
   service/client capture, cookie/CSRF, pagination, later-page publisher denial
   and cleanup on [both architectures](https://github.com/colophon-group/jobseek/actions/runs/37653235733).
   The grouped retained-configuration screen admits 55 additional boards: 33
   API browser, 20 DOM and 2 Inline (7,295 total / 587 remaining **candidate
   only**); production remains 7,240 / 642.
   [PR #10350](https://github.com/colophon-group/jobseek/pull/10350) contains the
   grouped candidate at `0dec76982ac1163851a0f5e0da7ae38dcb3fb8c1`. Its real
   Lightpanda 1.0.0 API and action service/client fixtures passed on both Linux
   architectures in [run 37656522604](https://github.com/colophon-group/jobseek/actions/runs/37656522604).
   Final full local suites and current required PR checks are merge prerequisites;
   fresh staging and supported deployment remain pending. See the
   [native API browser candidate proof](evidence/go-api-browser-native-candidate-2026-10-07.json).
   The same release now carries DOM/Inline wait/evaluate pipelines, including
   sequential order, asynchronous function/Promise execution, optional and
   required failure, deadline/cancellation, whole-document recapture, publisher
   checks before further actions and cleanup before output. Remaining click,
   wait_for, repeat, overlay and pagination contracts still need verified
   Lightpanda behavior. The grouped candidate leaves DOM 219, API sniffer 116
   and Inline 6 in this retained census. See the
   [grouped candidate proof](evidence/go-grouped-browser-native-candidate-2026-10-07.json).
   Preserve configured resource policy, proxy and browser identity requirements.
   Add remaining compatible HTTP/API provider families in groups; do not ship
   one small type per migration iteration. Recruiterbox's dedicated detail
   scraper remains a separate contract; qualified JSON-LD assignments already
   retain native detail ownership.
3. Replace mandatory Python consumers in compatible groups: worker/browser
   `crawler run`/`run-browser`; deployment `crawler sync` and schema preparation;
   activation/epoch/reaper and maintenance commands. Reuse the existing Go queue,
   persistence and operator contracts. Keep useful Python reference/labelling
   tools isolated outside production crawler execution.
4. Observe comparable whole-service CPU/RAM/density/attributable cost and
   normal-schedule freshness/conservation. Exercise supported full cold reversal
   and retain rollback evidence/images for the observation window.
5. Remove production Python, Playwright, Chromium and legacy runtime-only assets
   once all enabled profiles and mandatory consumers have replacement authority.
   Verify the complete native image, startup, deployment and maintenance paths.

Batch compatible implementations into useful releases. Finish each rollout with
serving ownership and real completions before treating it as migration progress.
Add infrastructure or fixtures only for a changed contract or observed failure.
At each checkpoint record remaining enabled profiles and production Python
consumers. Keep implementation, staging, serving and observation status distinct.

## Operational handoff

Use the installed source-bound drivers and immutable images. Respect deployment
holds, scheduled reconciliation, the mutation lock and exclusive SQL barriers.
Wait for live legacy leases to expire naturally. If expired tokenless monitors
block activation, preserve the exact pending receipt and cold lane, use the
existing source-bound maintenance reaper, then retry **activate with the same
plan/projection hashes**. `recover-pending` cancels an incomplete activation and
restores the full legacy stack; it does not resume activation. Do not clear
leases/fences, kill foreign one-offs or partially restart writers to force progress.

Earlier production observations remain in
[source17 evidence](evidence/go-native-family917-production-2026-10-04.json),
[v0.13.913 evidence](evidence/go-native-family913-production-2026-10-03.json)
and Git history. Follow [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md)
and [Hetzner maintenance](16-hetzner-maintenance.md) for deployment and recovery.

## Delivered four-provider implementation

Release **0.13.963** compiles 107 profiles with Manatal, HRMOS, Recruiterbox and
jobs.ch/jobup. It preserves
Manatal's advertised-count/no-progress rules and rich fields; HRMOS canonical
URLs, listing markers, explicit emptiness, totals and current-page checks;
Recruiterbox authoritative totals, partial-inventory protection and inactive
account evidence; JobCloud company aliases, portal/localized identities and
exact pagination completeness;
source-bound publisher observations and whole-inventory failure; and supported
cold retirement after interrupted writes or acknowledgment loss. The existing
queue, processors, persistence and maintenance paths remain the execution
contracts. See the [candidate evidence](evidence/go-native-provider-batch-six-candidate-2026-10-07.json).
Required checks, merge, immutable rollout, fresh admission, supported ordinary
activation and independent serving proof passed. New-provider completions and
full freshness/conservation proof remain pending at this checkpoint. Preserve every
remaining board until its replacement contract passes.
