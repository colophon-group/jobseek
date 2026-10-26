# Go and Lightpanda migration delivery plan

## Current delivery and next batch — October 27, 2026

Production is healthy on **0.13.1098**, source
`1051a83bd70d3b236320202a008dfd7257fb9f5f`, through
[PR #10918](https://github.com/colophon-group/jobseek/pull/10918) and the original
full deployment workflow **40413547691**, including promotion. Independent
readback at **22:55 UTC on October 26** verifies seven healthy retained services,
six HTTP200 endpoints, exact immutable images and a byte-identical
runtime/committed environment. The committed data revision is also `1051a83bd7`,
preserving both Vinfen rows from the earlier `d8eaa5dadf` company publication.
The actual enabled catalogue contains **8,136 boards**. Native owners remain cold;
the last observed epoch is **375**. The earlier **0.13.1093** recovery source
`f2e84b142cd14cdde0b11a9d8bd3f1a206f8ca3d` restored service through
[PR #10906](https://github.com/colophon-group/jobseek/pull/10906).

The deployment and its rollback had stopped all writers when Bajaj's **10,025**
schedules exceeded the old 10,000-row repair ceiling. The recovered helper
streams the complete inventory into a byte-bounded atomic payload, validates
every row and retains the original whole-inventory Lua preflight. The 10,025-row
forward/reverse and late-invalid-record regressions pass on real PostgreSQL and
Redis. The original complete rollout restored service; no individual restart or
hand edit of SQL, Redis, ownership receipts or environment was used.

[PR #10907](https://github.com/colophon-group/jobseek/pull/10907) moves five ATS
inventory host helpers into the existing Go binary: bounded logs, registry
snapshots, GitHub App tokens, status publication and network acceptance. Its
first host installation stopped before replacement because `/run` is mounted
`noexec`. The repaired installer preflights its root-owned binary in protected
executable `/usr/local/libexec`, preserving `/run` hardening. Original workflow
**40407317067** succeeded against independently healthy 1096. Independent readback
confirms native capability, root/0755 binary, all five Go wrapper delegates,
successful report-only acceptance, cleared acceptance pin, and active/enabled timer.

[PR #10908](https://github.com/colophon-group/jobseek/pull/10908) merged as
`151ddbf4331548910dec0efd72faec5387a50372`, version **0.13.1095**, with both required
checks green. Its crawler rollout stopped before quiescence on the original WAL
archive gate; 1096 subsequently passed that gate and deployed the accumulated
changes. The earlier ATS workflow timed out waiting for 1095; the successful
current-release dispatch above supersedes it. The release admits API-sniffer metadata selection, Oracle
title/metadata delegation, and RSS location/employment/date delegation. The
shared detail writer preserves Python's metadata availability check without
adding canonical column writes. All 18 frozen preparation cases and the real
selected-writer/queue/publisher tests pass. Its new-catalogue SDK result is
**230 monitors / 232 details** across the existing 37 families.

The deployed **0.13.1096** shared batch handles **three** additional monitor
configurations together:

- Workday applies the existing canonical URL transform after CXS discovery,
  preserving independent detail scheduling. Robots discovery accepts the original
  `siteMap.xml` spelling and retains the published site's case.
- Lever schedules explicit description/date enrichment through the existing
  selected-field detail writer. Listing refreshes preserve scraped descriptions;
  discovery retains structured salary emitted by the existing parser.
- Tupu360 uses the retained direct rich-row writer with a strict static parser.
  Its authoritative count, constant total, card fields, unique IDs and pagination
  must all validate before any inventory is published. Later failures and publisher
  reservations retain no partial inventory or absence authority.

All **16** original Python Tupu360 parser cases match. Fresh original-wrapper
public captures and sealed Go replay match every returned URL and available
field: **92 Tupu360 postings / one request**, **538 transformed Workday URLs /
28 requests**, and **54 Lever postings / one request**. Real migrated
PostgreSQL/Redis tests cover separate detail queues, new/touched/relisted
content authority, four-miss absence, later-page failures and reservations.
The complete actual 8,134-board SQL/cache SDK check now admits **233 monitors /
232 details** among the 234 new rows, proposing **8,091 monitors / 8,099 details**
with zero binding mismatches. Three new-catalogue assignments remain:
Miniso Argentina monitor, Ericsson China detail and R.R. Donnelley India detail.
These candidate checks grant no staging or serving authority. The retained old
frontier, full route proof, freshness, resource evidence and final reversal also
remain required.

See [the three-family qualification evidence](evidence/go-native1096-workday-lever-tupu360-candidate-2026-10-26.json).

The complete recovered-source observation and SDK replay cover **1,957,037
actual scheduled URLs** for a provisional **8,088-monitor / 8,099-detail** cohort,
with zero cached identity mismatches. All actual fallback steps and intervals
validate. **Ten** legacy lane duplicates are recorded for the existing original
cold-activation normalizer; require strict canonical lane proof afterward.
The newer non-atomic queue observation contains **3,298,544 unique IDs**. All
**2,402** IDs absent from the older 3,269,016-ID scan have exact current
canonical dispositions: 1,157 inactive, 686 no longer scheduled, and 559 already
queued or inflight again. Of 17 observed gaps, nine remain active and unqueued;
all nine are in the previously captured 757-gap set. Preserve these exact private
IDs and complete their original cold-lifecycle dispositions. This does not yet
establish postactivation conservation, freshness or public content parity.

The original renderer retry recovered source `00a9e859` with verified **1 GiB /
no swap** limits. Lightpanda **1.0.2** remains the latest stable release. Its four
frozen text-visibility failures remain unresolved; preserve all 21 original
expectations and draft #10799. A private canvas measurement probe is diagnostic,
not proof of equivalent text rectangles or a promotion.

Upstream changed Range geometry on October 25. The newer official snapshot
**1.1.0-nightly.11106+659958a24** passes **all 21 unchanged original wait-state
fixtures** and both physical Linux amd64/arm64 protocol/isolation suites.
[PR #10799](https://github.com/colophon-group/jobseek/pull/10799) merged as
`e20eb57e998a0d0e556cdc13fcf910648fc48e98` after whole-head Required CI, Crawler
Deploy Gate, public field/URL comparisons and the ARM64 benchmark job passed.
Its official OCI index and executable hashes are immutable; builds resolve no
mutable tag or GitHub asset URL. The later nightly Git ref differs from the
executable build revision, which is recorded separately. Original renderer
workflow **40416530488** is running. Keep main unchanged through its exact-main
reattestation, and require independent source/image/binary/version/1 GiB/no-swap
and protected-service acceptance. Production retains the accepted engine until
that rollout completes. See
[the immutable snapshot evidence](evidence/lightpanda-nightly-20261026-source-qualification.json).

Continue in this order:

1. Finish the qualified runtime slices with exact-head Required CI, Crawler
   Deploy Gate and fresh draft/base/hold/operator checks; use the original full
   rollout and independently verify the final source. Avoid unnecessary native
   activation/retirement cycles between runtime rollouts.
2. Use the refreshed actual 8,136-board SQL/cache configurations and all actual
   scheduled URLs to qualify the complete cohort, including new identities.
   Reconcile the exact **3,269,016** prior unique queued IDs and **757** preexisting
   scheduled-but-unqueued routes; matching totals do not establish conservation.
3. Reconcile the renderer source, stage the reviewed selectors, and use the
   installed B0 and ordinary stage/inspect/activate commands. Decode the complete
   staged payload and verify actual serving authority. Complete native ATS host
   rollout and timer acceptance against the same final release.
4. Finish remaining enabled configurations in shared batches, latest-Lightpanda
   compatibility, sustained freshness, comparable whole-lane resources/cost,
   supported cold reversal and its recovery window. Then retire production
   Python, Playwright, Chromium and runtime-only assets, keeping useful offline
   Python tooling. The full-delivery goal remains active.

See [recovery and candidate evidence](evidence/go-native1093-recovery-and-enrichment-candidate-2026-10-26.json).

## Rendered continuation candidate — October 26, 2026

**0.13.1096** is deployed from [PR #10916](https://github.com/colophon-group/jobseek/pull/10916).
The original WAL operational preflight passed after its natural one-hour window;
no counter reset or telemetry bypass was used. Recheck current source, holds and
readiness before any subsequent merge or rollout.

The deployed **0.13.1098** batch in
[PR #10918](https://github.com/colophon-group/jobseek/pull/10918) closes the remaining three new-catalogue
assignments:

- Miniso's exact browser expression projects the first JSON-LD ItemList and
  filters talent pools through the existing held-document URL-only path. All
  **18** original offline Chromium expression/stream cases match. Missing or
  malformed data fails before inventory publication. Its complete positive
  original **10-URL** reference matches physical Lightpanda. Later original
  Chromium attempts also failed before JSON-LD appeared; retain that variability
  and require natural serving freshness after activation.
- Nextdata/embedded details delegate the selected fields already supported by the
  shared writer. Real PostgreSQL/Redis tests cover all eight selected Nextdata
  fields, canonical preservation and settlement. Ericsson China's CSV adds an
  optional click of the site's Chinese-language control and a required section
  wait before the original extraction. This preserves the original three public
  title/description/location/employment results across browser localization.
- R.R. Donnelley India's exact original configuration joins the existing qualified
  rendered-control map after **three** positive original/native public field
  comparisons. The original legacy option remains in the immutable ownership
  hash. Other configurations still require their own qualification.

The **10 Miniso URLs plus six public detail samples** succeed with official
Lightpanda **1.0.1 and 1.0.2**. The actual SQL/cache SDK check now admits all
**234 new monitors and 234 new details**, proposing **8,092 monitors / 8,101
details**, with zero binding mismatches. The CSV locale repair has its own
registry/action test and regenerated census. These are candidate checks; recapture
actual published metadata and every newly owned scheduled route before staging.
The older **39-monitor / 30-detail** frontier and three separately tracked B0
boards remain, together with exact queue conservation, natural freshness,
whole-lane resource/cost evidence and final cold reversal. General 1.0.2 promotion
still requires the four retained visibility regressions to pass unchanged.

See [rendered candidate evidence](evidence/go-native1097-rendered-catalogue-candidate-2026-10-26.json).

## Retained variants in the same rollout — October 26, 2026

The combined candidate additionally qualifies **eight exact original DOM detail
configurations**: ALTEN Belgium, Italy, Netherlands, Portugal and Switzerland;
Only About Children Australia; Glasswall; and UNHCR Thailand. **22 positive
original details match every available field with official Lightpanda 1.0.1 and
1.0.2**. UNHCR has one active posting, so its evidence contains that one sample.
Full original configuration hashes and HTTPS hosts scope these admissions;
legacy options remain in ownership hashes and configured proxy requirements
remain enforced. The public physical comparisons use direct verified TLS;
configured proxy availability and natural execution remain separate gates.

The same batch preserves the original absent-body API paginator wire request
without inventing a POST body. Four frozen original Python references match
method, URL, body, complete inventory and truncation; a missing body still cannot
bootstrap token refresh. The rendered DOM path accepts the original `my_job_shop`
annotation while retaining link and explicit-empty proofs. Real PostgreSQL/Redis
tests cover complete/empty publication, missing-marker/publisher/incomplete
failure authority and independent detail queues. Existing rendered-detail and
static full-body contracts pass alongside them.

The complete refreshed **8,136-board** SQL/cache check proposes **8,094 monitors /
8,111 details**, with zero missing caches or effective binding differences. It
preserves the prior floor, adds the eight qualified details and the two newly
published Vinfen rows using existing generic implementations. Other newly
SDK-eligible profiles remain outside this cohort. Persistent Systems currently
returns original HTTP503 failures; Fressnapf's physical navigation is unproved;
ALTEN Spain has a location mismatch; Sophia Genetics returns HTTP202. Preserve
those failures and existing enabled configurations.

Both batches share final-head CI and one complete rollout. Recapture the actual
published Ericsson locale controls, every added scheduled route and the full
ownership payload before activation. Then finish exact-ID conservation, natural
freshness, latest-engine compatibility, resource/cost proof, cold reversal and
production Python retirement. See
[retained-batch evidence](evidence/go-native1098-retained-variants-candidate-2026-10-26.json).

## Three further explicit JSON-LD navigation replacements

The next data-only batch replaces obsolete Chrome/proxy controls for Citadel
Securities, Edmond de Rothschild internships and CGI Njoyn with explicit direct
verified-TLS navigation through the existing rendered JSON-LD profile. Original
waits, timeouts, extraction, defaults and enrichment remain. CGI's origin-block
fallback is removed because the candidate already selects direct navigation.

Each source has three positive actual postings on both official Lightpanda 1.0.1
and the latest official 1.1.0 nightly snapshot. All **18** comparisons match the
unchanged original browser-neutral JSON-LD parser and original scrape enrichment
against Go's existing equivalent preparation, including full description and
structured extras. One raw-stage description difference disappears at that
equivalent preparation boundary: both append the same qualifications section.
No parser or writer change is needed. Original Chromium fetches were blocked;
these references compare original parsing on native-held documents, without
claiming positive original Chromium navigation.

The 1098 runtime is independently healthy. Keep this batch unmerged until the
exact-main renderer workflow above completes, then use the original CSV publisher.
Recapture canonical configurations,
caches and every added scheduled URL before extending ownership. The underlying
three monitors and detail lanes remain. Indeed's failed native probe is excluded.
See [the complete field and transport evidence](evidence/three-jsonld-normal-navigation-candidate-2026-10-26.json).

## Historical publication blockers — before recovered 0.13.1093

Before the recovered release, production remained on **0.13.1086**, source `6e1d0ebb3f2294b95e94e26854d5d4fefd282d52`,
epoch **374**. The supported deployment refuses to replace an active native
owner; the failed 0.13.1089 and 0.13.1091 deployment attempts do not establish
new serving authority. Retire ordinary ownership, roll back B0, and clear only
the reviewed selectors through their original drivers before dispatching the
exact checked source. Keep all source/image/receipt guards and mutation locks.

The [source reconciliation evidence](evidence/go-retirement-source-reconciliation-2026-10-26.json)
records a complete streamed observation of the outgoing cohort: **1,923,012
scheduled detail routes**, **3,264,449 non-atomic queue memberships**, and **zero
queued-posting cache mismatches**. The original 0.13.1089 five-monitor/four-detail
proposal passes the Go SDK against every captured route. **750 scheduled postings
across five boards remain unqueued**, with all five cached route fields absent;
an exact-ID follow-up confirms that this predates the next handoff. Retain those
IDs privately and prove dispositions independently; neither a matching total
nor the SDK check proves queue conservation or freshness.

Current main `b32ee29695f8ddabec0243ee167a97e3f521641c` contains **8,134 catalogue
rows**: 234 additions, ten changed existing configurations and the reviewed
replacement of `ericsson-pinpoint` by Ericsson's Eightfold and China sources.
Those rows are not yet a production census. Reconcile publication against the
retained cohort, including the four additional existing-board reconfigurations,
and qualify new rows before claiming native ownership. The retained cohort plus
the five-monitor/four-detail batch, less the replaced board, proposes **7,858
monitors / 7,867 details**; read back actual identities after publication.

Full prepared metadata exposes three compatibility failures missed by static
CSV-only fixtures: Becton Dickinson's TalentBrew monitor and Nokia's Oracle HCM
monitor/detail retain the old discovered `sitemap_url` through SQL sync. The
0.13.1092 fix accepts and binds that retained string without using it as a request
target, while preserving rejection of unknown options. Nokia also selects
`job_location_type` enrichment; admit that field through the existing Oracle
parser and shared enrichment writer, with canonical settlement tests. Its detail
configuration must retain the matching tenant/site overrides for already scheduled
vanity URLs. Validate full SQL/cache
metadata and actual scheduled URLs again on the final source, then stage and
activate the complete cohort through the supported cold lifecycle.

Lightpanda **1.0.2** remains the latest stable upstream release at this checkpoint.
Resolve its retained visibility failures before promotion; the full migration
goal, final Python retirement, whole-lane resource/freshness evidence and cold
reversal remain open. Do not count this publication recovery as full delivery.

## Active full replacement — October 23, 2026

The owner resumed complete production replacement and retirement. The Codex goal
is active; [epic #7935](https://github.com/colophon-group/jobseek/issues/7935) is
the current checklist and [#7966](https://github.com/colophon-group/jobseek/issues/7966)
is reopened for final retirement. Preserve useful isolated offline Python.

Recovery [#10794](https://github.com/colophon-group/jobseek/issues/10794) and
[#10796](https://github.com/colophon-group/jobseek/pull/10796) restored the complete
cohort and explicitly cleared the hold. Independent readback at **2026-10-23
16:24:11 UTC** verifies **0.13.1086**, source
6e1d0ebb3f2294b95e94e26854d5d4fefd282d52, epoch **374**, **7,854 monitors /
7,864 details** across **7,901 enabled boards**. SQL ownership and the
original-derived Redis projection match exactly; retained membership is exact.
No open crawler deployment hold applies at this observation. Recheck state before
any merge or production change.
The retained checkpoint uses self-hosted Lightpanda 1.0.1. Stable **1.0.2** was
released on October 23 and is independently source-qualified in
[#10798](https://github.com/colophon-group/jobseek/issues/10798) /
[draft #10799](https://github.com/colophon-group/jobseek/pull/10799).
Four original visibility cases pass with official 1.0.1 but fail with 1.0.2 on
macOS and both physical Linux architectures. The original 1.0.1 reference passes all 21 cases. Keep the renderer
transition separate and unpromoted until compatibility is established.

The current [sanitized exact inventory](evidence/go-retirement-frontier-2026-10-23-1624.json)
records **44 monitor / 34 detail assignments** outside ordinary ownership.
The earlier [13:42 inventory](evidence/go-retirement-frontier-2026-10-23.json)
recorded 43 / 33 across 7,900 enabled boards and is historical. Newly enabled
Mandarin Oriental adds one sitemap monitor and one rendered JSON-LD detail;
new catalogue entries do not acquire native ownership automatically.
Three B0 boards (Browser Use, bunq, Algorized) remain separately tracked for
final lifecycle reconciliation. Census and compiler success do not prove
current content parity, whole scheduled-route coverage or sustained conservation.

The sanitized evidence records UUIDs, structural controls, snapshot config
fingerprints and issue membership without credentials or raw configuration.
Fingerprints identify the dated census and do not grant ownership.

| Shared batch | Monitors | Details | Issue |
|---|---:|---:|---|
| Native admissions, staged cleanup and ATS runner | 4 | 3 | [#10788](https://github.com/colophon-group/jobseek/issues/10788) |
| Proxy and persistent sessions | 16 | 24 | [#10792](https://github.com/colophon-group/jobseek/issues/10792) |
| Nonproxy browser monitors | 14 | 0 | [#10790](https://github.com/colophon-group/jobseek/issues/10790) |
| HTTP inventories, document policies and static binding | 10 | 1 | [#10789](https://github.com/colophon-group/jobseek/issues/10789) |
| Nonproxy rendered details | 0 | 6 | [#10791](https://github.com/colophon-group/jobseek/issues/10791) |
| **Total outside ordinary ownership** | **44** | **34** | |

Every assignment occurs in one group. Different assignments on the same board
can have different blockers. Bright Horizons monitor is staged, while its detail
remains in the proxy group; an empty comparison did not qualify it. Groove Quantum
and Orange Quantum Systems contribute both surfaces to the staged group.

The combined **0.13.1089** candidate in
[PR #10797](https://github.com/colophon-group/jobseek/pull/10797) includes the ATS
port and five cleanup assignments from
[PR #10707](https://github.com/colophon-group/jobseek/pull/10707), native maintenance
from [PR #10795](https://github.com/colophon-group/jobseek/pull/10795), and the document
contracts below. Keep the component PRs available for review; supersede them only
after the complete combined candidate is verified and delivered. This gives one
source for final Required CI, installed-image checks, native command acceptance,
full-cohort staging and supported deployment/cold handoff.

The candidate preserves all **7,901** current-main catalogue entries, including
Mandarin Oriental, and regenerates the deterministic census. Its ATS compression
dependency uses the patched release. It includes recovery main
`6e1d0ebb3f2294b95e94e26854d5d4fefd282d52` and its clock fixture correction. Repeated
public ECR HTTP429 failures blocked the old sitemap build comparator; its exact
Go image index was independently verified on official Docker Hub and the build
source changed without changing the toolchain version or digest.

The old ATS-only proposal was **7,857 / 7,866**, conditional on admission against
the old census. Refresh the actual post-recovery registry and staged payload to
derive the combined floor; newly published catalogue entries are not automatic
native ownership. Compilation and unverified empty results are not content proof.

[#10793](https://github.com/colophon-group/jobseek/issues/10793) tracks actual
production entrypoints across Compose, timers, protected workflows and recovery
callers. Go paths already exist for migrations, sync, exporter, R2 drain,
reconciliation and provider repairs. Finish command bootstrap and packaging.
The first new implementation, [PR #10795](https://github.com/colophon-group/jobseek/pull/10795), moves scheduled maintenance
preflight and host hygiene to the existing source-bound Go deployment tools.
Exact-source/checksum verification, host mutation locking, operation budgets,
Typesense credentials and independent hygiene accounting remain. The native audit
preserves resource selection, age thresholds, advisory cleanup and exit codes;
it never performs cleanup. Python remains an offline parity reference used by
the separate host-hygiene qualification workflow. This is candidate work,
not a deployed crawler replacement. The maintenance changes are now included in
the combined **0.13.1089** candidate; deployment and main merges require fresh exact-head gates
now that the documented recovery has cleared. Follow-up fixes bound streamed inventory output and keep the
reconciliation ordering proof on the native command.

The first [HTTP contract implementation](https://github.com/colophon-group/jobseek/tree/fix-crawler/native-http-contract-batch)
ports mutable PDF response fingerprints into the existing static DOM and
independently verifying PDF paths. Original Python identity oracles and real
private PostgreSQL/Redis canonical, failure and publisher-policy checks pass.
Current FIBA and Tesco captures do not establish positive inventory qualification;
no ownership or group-count reduction is claimed. The remainder of #10789 and
installed-image/current-content admission are still required.

Current World Gymnastics replay now agrees on two headquarters and two excluded
member-federation documents, including both complete job field maps. Native
legacy empty selectors retain the original bounded full page instead of cutting
the links after 500,000 characters. Two current detail bindings also have positive
content evidence: correct Flex/Jetcool's Onlyfy reader to its actual Workday source
(all six populated fields agree), and remove VUMC's obsolete stealth flag after
original Chromium and native 1.0.1 agree on all four populated fields. Preserve
VUMC's original lean policy by reusing the existing native DOM resource contract;
original policy values stay in immutable bindings and unknown policies still fail.
See [detail evidence](evidence/go-current-detail-bindings-2026-10-23.json).

Mandarin's unchanged sitemap configuration produces **783 identical canonical
URLs** through the original Python and actual native Go worker transport/parser.
Its original Chromium JSON-LD scraper returns a complete 5,694-character
description, while physical native Lightpanda 1.0.1 returns **HTTP202**.
The monitor is a candidate admission; the detail remains unqualified.
See [current evidence](evidence/go-mandarin-current-binding-2026-10-23.json).
The combined candidate proposes **five monitors / four details**, including only
Mandarin's monitor: conditional floor **7,859 / 7,868**, remaining **39 / 30**.
These are proposed counts; the current serving floor remains **7,854 / 7,864**.

The first complete CI run found two old test expectations inconsistent with the
current change: DOM lean/auto details and the corrected Flex/Onlyfy count.
The expectations now retain DOM full-config policy binding, JSON-LD/unknown-policy
rejections and the separate current Workday/old-Onlyfy negative cases.
Required CI must pass again at the final source before delivery.


After each admission, preserve the entire enabled/owned floor, replay actual
scheduled routes, decode every staged assignment, then use the original immutable
deployment and quiesced ownership workflows. Verify source/images/receipts,
SQL/Redis authority, every actor and exact-ID queue dispositions after activation.
Respect current holds and changed-head/operator decisions.

Keep [#10693](https://github.com/colophon-group/jobseek/issues/10693) open for
sustained natural execution, queue conservation and freshness. The October 17
checkpoint recorded 1,637,563 owned postings over 24 hours overdue; this is
historical, not a fresh October 23 measurement. Keep first-time priority and
publisher policy. The last recorded Webshare responses are HTTP402; recheck
access before content qualification, without treating probes as payment/account
authority or blocked publishers as empty inventories.

[#8648](https://github.com/colophon-group/jobseek/issues/8648) retains bounded
resource evidence. Eight synthetic B0 matched pairs do not establish whole-fleet
savings. Complete broader comparable CPU/RAM/density and attributable-cost proof.
Full retirement requires every enabled configuration and B0/ordinary lifecycle,
native operational consumers, sustained correctness/freshness, broader resource
evidence, final supported quiesced reversal and its recovery window. Then remove
production Python, Playwright, Chromium and obsolete runtime-only assets.

<details>
<summary>Historical October 17 delivery checkpoint and original exit criteria</summary>

Deliver the **full migration**, preserving every enabled board, canonical content,
publisher policy, queues and demonstrated freshness. Production execution and
operational consumers must use Go with the latest stable self-hosted Lightpanda
or proved native HTTP/API routes. Retire production Python, Playwright and
Chromium after replacement coverage and supported recovery are established;
retain useful isolated offline Python. The goal remains **active and incomplete**.

## Verified serving checkpoint — October 18, 2026

Release **0.13.1084**, source `1741d2319db7c3eaa55dc266517fc701e5888ced`, serves
**7,854 monitors / 7,864 details** at epoch **372**, preserving the entire previous
7,850/7,864 floor. Self-hosted **Lightpanda 1.0.1** is verified against the official
Linux binary and immutable image. See the [official release](https://github.com/lightpanda-io/browser/releases/tag/1.0.1).

Both original deployments and supported B0/ordinary activation pass. All 15,718
immutable assignments and four new configuration bindings are attested. Strict
independent source/image/receipt/SQL/Redis/health readback passes for all ten actors.
Full postactivation replay captures all **1,746,797** owned scheduled URLs queued,
with zero owned cache mismatches or captured gaps. Every before-only queued ID
is currently accounted for as a retained unscheduled canonical record.

The [serving checkpoint](39-go-lightpanda-four-monitor-checkpoint-2026-10-18.md)
and [aggregate evidence](evidence/go-native1084-serving-checkpoint-2026-10-18.json)
retain scope, inherited failures and non-atomic-observation limits. Three current
Linux successes contain 2,387 URLs. The fourth newly owned monitor preserves its
inherited HTTP403 failure; availability is not restored. No deployment mutation
is pending. The enabled frontier, excluding three B0 boards, is **43 monitors /
33 details**. Every board remains enabled.

## Deliver the next substantial batch

The combined **0.13.1087** candidate ports the full ATS inventory command to
Go and proposes **three monitor / two detail** configuration admissions together.
It preserves the full 7,854/7,864 serving floor and targets **7,857 / 7,866**.

The October 23 refresh preserves the full 7,901-board main catalogue, including
Mandarin Oriental, and all three original qualified configuration replacements.
It regenerates the deterministic census rather than choosing either side of a
generated-file conflict. Original 0.13.1085 qualification remains historical
evidence; exact refreshed-head CI and fresh serving admission remain required.
Release 0.13.1086 is reserved by the separate source-bound retirement recovery
in [#10796](https://github.com/colophon-group/jobseek/pull/10796). Preserve its
production recovery operation [#10794](https://github.com/colophon-group/jobseek/issues/10794):
no deployment or ownership mutation is part of this catalogue refresh. Incorporate
the reviewed recovery change and revalidate current main before rollout.

The new command carries over the existing 47-family registry, source validation,
last-known-good caches, projected Parquet impact calculation, conservative
identity/deduplication, SQLite ledger, preserved issue text, rate handling and
capped queue modes. It is wired into the immutable image and host runner with
existing write gates, credentials, budgets and completion events. Retained older
images keep their explicit rollback command during the recovery window.

Go matches every parsed field in the live **80,390-row / 47-family** inventory.
Three complete real Parquet families covering **7,815 job rows / 57 companies**
match Python's summaries, company impacts and exact cache bytes. Frozen Python
cache/ledger/identity/queue oracles and fake-HTTP full CLI tests pass, along with
52 retained original/host contracts. These tests create no real GitHub issues.

The configuration batch has complete original/native 12-URL monitor inventories
and six usable full-content detail comparisons, actual canonical processing,
absence guards and preserved `never` rescrape SQL/Redis behavior. One empty JSON-LD
case is excluded. Do not count empty maps as usable content.

Finish final whole-head CI/Gate and installed-image checks, freeze the combined
configuration references, refresh every actual route, decode the full immutable
staged plan, then use the original deployment/cutover workflows. Verify serving,
exact-ID queue dispositions, natural execution and ATS runner acceptance. See
[candidate evidence](evidence/go-native1085-ats-inventory-candidate-2026-10-18.json).

## Continue coverage and operations together

- Qualify the remaining browser/API/action/configuration contracts in substantial
  shared batches. Reuse existing parsers, controllers and recovery commands;
  retain full identities/configs, actual non-null fields, publisher policy,
  canonical writes and failure queues. Unverified empty responses stay unqualified.
- The last recorded configured Webshare probes return HTTP402. Restore provider
  access before claiming positive proxy content qualification. Keep existing
  failure evidence and enabled boards; external evidence cannot authorize account
  or payment changes.
- Audit actual remaining Python entrypoints and ownership/configuration lifecycle
  after registry publication. Finish the native consumer and recovery paths before
  removing their runtime dependencies. Keep the final reversal supported throughout.

## Finish freshness, resource proof and retirement

At 22:22 UTC on October 17, **1,637,563** owned scheduled postings are over 24 hours
late and **1,582,101** over 48 hours. Preserve first-time priority; measure useful
completions, lateness, failure dispositions and queue conservation over sustained
intervals. Keep [issue #10693](https://github.com/colophon-group/jobseek/issues/10693)
open. Do not turn unavailable publishers or failures into empty inventories.

Current matched whole-lane B0 fixtures pass eight candidate/control pairs with
equal canonical digests and exact cleanup, bound to the production runtime tree.
They are synthetic. Complete broader comparable matched-work CPU/RAM/density and
attributable-cost evidence across relevant actors with explicit limits.

Full-delivery exit criteria remain:

- Every enabled monitor/detail configuration has a proved Go replacement with
  complete canonical fields, policy, queues, freshness and failure behavior.
- Deployment, data publication, native ownership lifecycle, maintenance and
  recovery use committed workflows with exact source/image identities.
- Comparable resource/density and attributable-cost evidence covers relevant
  execution and operational actors.
- Exercise final supported quiesced reversal and establish its recovery window,
  then remove production Python, Playwright, Chromium and runtime-only assets.
  Useful isolated offline tooling may remain.

Update this plan and portable checkpoint after material delivery. Earlier
chronological evidence remains in Git history and the linked historical
checkpoints; probes, compiler readiness and an implemented command are not
serving or full-completion claims.

</details>
