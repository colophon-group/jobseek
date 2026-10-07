# Go and Lightpanda migration delivery plan

Updated 2026-10-07. The full migration goal remains active.

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

Combined RSS/Inline/DOM release **0.13.962** merged in
[PR #10348](https://github.com/colophon-group/jobseek/pull/10348) at
`d2fa77713308f17eeb38fb06de060dc6b67dc595`. Its103 compiled profiles add
SuccessFactors legacy XML, Generic summaries, affine rendered RSS pagination,
rendered Inline document inventories and explicit DOM empty-state proof.
Exact reviewed-head full race suites passed: ordinary-worker523.220 seconds
and ordinary-queue313.876 seconds. Actual installed Linux raw XML/CDATA,
cookie affinity and cleanup passed on amd64 and arm64 in
[pilot37578575140](https://github.com/colophon-group/jobseek/actions/runs/37578575140).
Required CI and Crawler Deploy Gate passed before the bound merge.

Original [crawler deployment37580824070](https://github.com/colophon-group/jobseek/actions/runs/37580824070)
and [renderer deployment37581251505](https://github.com/colophon-group/jobseek/actions/runs/37581251505)
completed successfully, including crawler promotion. The renderer release
contains the new feed-session protocol and retains the latest verified stable
**Lightpanda1.0.0**, published October2. Both releases are bound to the merged
source and immutable image digests; see the
[rollout evidence](evidence/go-native-source962-rollout-2026-10-07.json).

Source960 ordinary retirement, supported B0 rollback and exact selector clear
succeeded. Independent cold212 readback verifies restored base writers,
cleared ownership, zero current fences and no orphan historical receipts.
Historical audit receipts remain. First source962 post-deployment readback
verified source/images and base writers, then stopped on the normal
cross-store reconciliation oneoff started by deployment. Let that job finish
under the shared mutation lock, then repeat the independent cold readback.
Fresh962 B0 and ordinary admission/activation remain pending; no previous
receipt, cohort or ownership plan grants reuse.

Offline source962 screening of the last source960 capture qualifies7,212
monitors,26 more than960, leaving670 monitors across the retained provider
backlog. These are configuration screening counts; fresh source962 production
admission must reconcile canonical/cache state and actual scheduled detail
routes. Source960 previously owned7,186 monitors and2,616 detail boards,
including1,463,861 scheduled postings. Every-profile freshness, whole-lane
resource/cost proof and complete Python retirement remain unfinished.

## Continue delivery

1. Finish source962 independent cold readback after normal reconciliation.
   Stage/activate supported B0, capture fresh source/receipt-bound canonical and
   cached configurations plus every scheduled detail route, then stage/activate
   ordinary ownership. Verify real scheduled completions, canonical fields,
   descriptions, publisher outcomes, SQL/Redis deadlines, health and freshness
   against the exact source/images/epoch/receipts.
2. Deliver the next compatible provider batch together. Candidate963 adds
   Manatal rich pagination and HRMOS URL-only listing/count pagination through
   the existing native worker and canonical processors:11 registry configs,
   ten enabled boards in the previous census. Actual Python parsing/traversal,
   canonicalPG/Redis publisher/settlement and cold-retirement checks pass; full
   suites and CI remain. Continue the large shared browser paths together:
   DOM239, API sniffer149 and Inline8 in the current offline screening.
   API-sniffer replay must retain captured response selection, auth refresh,
   browser cookies, HTTP fallback and bounded pagination. Group remaining
   small providers such as Recruiterbox with compatible HTTP/API work.
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

## Next grouped provider candidate

Candidate **0.13.963** compiles105 profiles with Manatal and HRMOS. It preserves
Manatal's advertised-count/no-progress rules and rich fields; HRMOS canonical
URLs, listing markers, explicit emptiness, totals and current-page checks;
source-bound publisher observations and whole-inventory failure; and supported
cold retirement after interrupted writes or acknowledgment loss. The existing
queue, processors, persistence and maintenance paths remain the execution
contracts. See the [candidate evidence](evidence/go-native-provider-batch-six-candidate-2026-10-07.json).
Production ownership awaits required checks, merge, immutable rollout and fresh
admission. Preserve every remaining board until its replacement contract passes.
