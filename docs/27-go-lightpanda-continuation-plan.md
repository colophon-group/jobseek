# Go and Lightpanda migration delivery plan

Deliver the **full migration**, preserving every enabled board and canonical
content/publisher/queue behavior. Production execution and operational consumers
must run in Go using the latest stable Lightpanda or proved native HTTP/API
routes. Remove production Python, Playwright and Chromium after replacement
coverage and supported recovery are established; retain useful isolated offline
Python. The goal remains **active and incomplete**.

## Current serving checkpoint — October 17, 2026

Release **0.13.1081**, source `2ba9c62a977307d1e8d5299ca5defbf24ee902ce`, serves
**7,842 monitors / 7,862 details** at epoch **368**, preserving the previous
7,840/7,859 floor. Latest stable **Lightpanda 1.0.1** was reverified October 17 at
14:14 UTC against the [official release](https://github.com/lightpanda-io/browser/releases/tag/1.0.1).
Exact qualified-head CI, both original deployments, supported staging/activation
and independent full serving checks pass.

Full postactivation replay validates **1,742,711 actual owned scheduled URLs**,
all captured queued with zero cache mismatches. Current canonical and publisher
checks account for every outgoing-only ID. All **9,239 original queue-gap IDs**
are now queued, including 851 current-epoch native completions. Natural post-fix
Workday/SmartRecruiters monitor publication remains unobserved; keep
[issue #10693](https://github.com/colophon-group/jobseek/issues/10693) open until
actual subsequent monitor publication and sustained conservation are proved.
These current non-atomic captures do not establish historical zero loss.

The [serving checkpoint](37-go-lightpanda-shared-route-checkpoint-2026-10-17.md)
and [aggregate evidence](evidence/go-native1081-serving-checkpoint-2026-10-17.json)
retain source/image/workflow bindings, failure/recovery evidence, full-config
qualifications and observation limits. No production operation is pending.

## Continue in substantial shared-contract batches

The current captured frontier outside ordinary ownership, excluding three B0
boards, is **55 monitors / 35 details**. Refresh the census when registry data
changes. Preserve every admitted member and leave unqualified assignments with
their legacy owners. Work on several families per shared transport/control batch;
provider implementations often already exist, while original config support or
complete rendering remains the admission blocker.

1. **Finish shared configured-proxy transport.** Build on the isolated pinned
   transport branch, incorporating source1081's publication fix before final
   qualification. The current interception experiment fails native cookie and
   redirect behavior under Lightpanda 1.0.1. The replacement prototype uses a
   parent HTTP/CONNECT bridge plus a Linux child network restriction. It has
   local bridge tests only and is unwired/unqualified. Prove actual Linux
   subprocess/UID/capability isolation, cookies, redirects, upstream credentials,
   destination pinning, verified TLS, cleanup and publisher policy through the
   installed Lightpanda. Audit API replay fallbacks for proxy bypass. Keep
   [draft PR #10643](https://github.com/colophon-group/jobseek/pull/10643) undeployed
   until upstream destination enforcement and positive content proof pass.
2. **Qualify a larger original-config batch across existing profiles.** Exercise
   DOM, JSON-LD, API-browser, inline and other applicable compiled consumers with
   actual original/native fields and canonical lifecycle tests. Config alterations
   are diagnostic hypotheses until original semantics are proved. Changed full
   configs and foreign sources must reject. Include every actual scheduled route,
   immutable staged members, queue conservation and publisher-policy cases.
3. **Resolve incomplete rendering with proved HTTP/API alternatives.** Concurrent
   Lifepoint inventories differ materially (183 original / 296 native, 174 shared);
   H3 Dynamics remains six original / five native. Direct HTTP alternatives
   currently yield zero URLs despite successful fetches. Neither route is
   qualified. Resolve pagination, source identity and completeness; fetch errors
   or zero extraction do not establish an empty inventory.
4. **Deploy and admit qualified batches through the original workflows.** Use
   complete exact-head Required CI and actual Crawler Deploy Gate, immutable
   images, current holds/authority, supported incarnation rotation and full
   source-bound staging/activation. Verify serving and current exact-ID queue
   conservation after each expansion. Preserve original failure evidence.

## Finish freshness and operational retirement alongside coverage

At 13:44 UTC, **1,623,769 postings are over 24 hours overdue**, including
1,566,722 over 48 hours. A bounded matched-endpoint observation records 20
successful monitors / 323 successful details with zero claim errors, restarts or
OOMs; this does not establish sustained freshness. Measure useful throughput,
lateness, failures and queue conservation over longer intervals. Observe the
finite first-time backlog draining before changing the existing strict first-time
priority contract: selected Workday/SmartRecruiters domains currently have
5,232/3,411 first-time details ahead of recurring monitors. Shared-domain queue
counts are not exact per-posting ownership proof.

Current deployment migrations, registry sync, provider repairs and reconciliation
already invoke Go. Audit every production command and maintenance consumer;
legacy Python branches still support retained rollback images. Prove the final
supported quiesced reversal and recovery window before removing those branches
and runtime-only images/assets.

Finish comparable **matched-work** CPU/RAM/density and attributable cost evidence
across all relevant actors. The current roughly 376-second live window averages
2.36 crawler CPU cores plus 0.12 renderer cores, but has uncontrolled startup and
backlog effects and excludes remote SQL, Typesense, R2/network and unrelated host
services. It is not a causal efficiency or final cost comparison.

## Full-delivery exit criteria

- Every enabled monitor/detail route has a proved Go replacement with complete
  canonical fields, publisher policy, queues and sustained freshness; coverage
  never drops to make migration appear complete.
- Deployment, CSV publication, native ownership lifecycle, maintenance and
  recovery use supported committed workflows with exact source/image identities.
- Comparable matched-work resource, density and attributable cost evidence covers
  the relevant execution and operational actors with explicit limits.
- Supported final reversal and the recovery window are proved, then production
  Python, Playwright, Chromium and runtime-only assets are removed. Useful
  isolated offline tooling may remain.

Update this plan and the portable checkpoint after material delivery. Earlier
chronological evidence remains in [Git history](https://github.com/colophon-group/jobseek/commits/main/docs/27-go-lightpanda-continuation-plan.md)
and linked checkpoint aggregates; do not turn preparatory probes into serving
or completion claims.
