# Go and Lightpanda migration continuation plan

Current checkpoint: **2026-10-31 13:22 UTC**. The full migration remains active.
Use current installed release and canonical registry state before saved evidence.
Historical raw evidence need not be preserved. This file is the portable handoff;
keep only active regression inputs, one current checkpoint and supported rollback
assets. Preserve unrelated work and the root checkout's `dump.rdb`.

## Installed production

[PR #11081](https://github.com/colophon-group/jobseek/pull/11081) is merged and
**0.13.1137** completed the [supported full rollout](https://github.com/colophon-group/jobseek/actions/runs/41201070769),
including promotion. Runtime source:
`6934e672a1e10ac5557c025af23f127197af3bbd`. Runtime contract:
`9f0dfc956d2276ef6d9f100e4e1d71661a4af5bc997ad88e8d22a6c71355f11c`.
Crawler image:
`sha256:05aebf3cc204f9256319433d849f9b97e127fb1db31fc894cd3c7ec281144172`.

Independent 13:22 readback verifies ordinary worker, reaper, exporter and drain
on this image, native commands, zero restarts/OOMs and the original budgets.
Processing started at 12:39:56. No legacy processor runs, and the processing
image exposes no Python, uv, Playwright or Chromium command. Native processing,
enrichment, PDF extraction, scheduling, persistence, failure handling, sync,
migrations, maintenance, currency refresh, export and description drain are
implemented. Useful offline Python remains separately packaged.

Normal registry publication has advanced independently: data revision
`65156848975ac156a9a638093f5d454bb30f7e96`, data contract
`9347eefbaec52ac531e9b9e7dc3066d3acd99a4e442d693941533034f4017eaa`.
There are **8,154 enabled boards**, routing epoch **404**, schema **0041** and
zero active migration plans. Three additions since the 8,151-board reversal
baseline require a refreshed fleet reconciliation; do not reuse its identity
hashes as proof of unchanged current membership.

At 13:22, 4,288 monitors succeeded and 4,520 were checked in the preceding hour;
2,988 were due and none overdue a day. This is a bounded snapshot, not proof of
universal freshness. The large pre-existing detail backlog remains open.

Current-generation storage readback at 12:42 covered 214 completed details,
20 provider groups and zero missing titles. All 212 nonnull posting hashes
matched uploaded SQL description rows; all 213 nonempty locale rows were marked
uploaded, with none pending/inflight. Ten mature Typesense records matched
canonical title/company. At 12:53, actual R2 GET bytes matched SQL UTF-8 HTML
SHA-256 for ten descriptions across six groups, with HTML content type and
33,871 total bytes. The endpoint/bucket fingerprint matched the installed drain.
These are bounded consistency samples, not universal completeness or proof that
every sampled object was uploaded by this generation.

The separately deployed host sampler retains source
`cad060906a8fc11b4036ee69ac0a81e26d2a26fb` and file hash
`826ebba933fdf321e07a6cd898f5bb68052fe501ee57717a7ea58f35b99d8590`.
Its normal six-hour attempt at 12:09 completed successfully. At 12:54 its cache
and host textfile were fresh, availability was one, and all six native execution
series were present. No extra scan, cache reset or schedule change was made.

## Lightpanda and operating constraints

Latest official release assets were checked at 12:20. Installed and pinned:
**1.1.0-nightly.11423+56980afde**, executable source
`56980afdec3ad177072e7a180cb05da838bc1744`. Stable 1.0.2 is older than these
October 31 [nightly assets](https://github.com/lightpanda-io/browser/releases/tag/nightly).
ARM64 executable SHA-256:
`97710997faea70bb0f22f9b07ba3a9701b47dbb6a71f679150b040e70c565cf3`;
AMD64 executable SHA-256:
`19f3fee25fb14bb75a1e0c1e0f8ab9cce842950544e4a144fbad3884c94b98a3`.
The [renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/41162854853)
installed ARM64 source `406d0f94ee919962da2ccb4196450344265c9445`, image
`sha256:c09939c34ba034ee9d68ec9176f19f8ddeea04bfa816ce595409b09386133af7`.
Recheck official asset identities before changing the renderer; do not identify
its executable by the moving Git tag alone.

Production remains **strict Lightpanda and Go HTTP/API**. No Chromium fallback,
forced provider work, manual SQL/Redis/env repair, raised capacity, reset failure
counter or changed deadline. Renderer: one CPU, 1 GiB, zero swap, 64 PIDs,
concurrency four, heap 128 MiB and responses 8 MiB, with mTLS and controlled
egress. Crawler: two CPUs, 1 GiB, GOMEMLIMIT 512 MiB, discovery 60, monitors 15
and ten candidate host domains. Redis ownership tokens are presence-only reads.
Never read, search or edit `profile_self_identifying_dom.go`.

## Verified comparisons and their limits

At PR #11080 source `c932b824c3056541cc53760f30d1336e58e2ae3a`, all **45 checks
passed**, with two skips. Required CI and Crawler Deploy Gate passed. Physical
ARM B0 admission passed all eight pairs with `admitted=true` and no reasons.
A changed head needs fresh required checks; these results bind this source.

Both complete-lane AMD64 scenarios finished six alternating arms with identical
canonical output, uploaded descriptions, matching search results and cleanup.
The control explicitly selects Python JSON-LD/DOM extraction and enrichment;
Go reaper/exporter/drain remain common measured actors. Earlier controls that
inherited Go extraction defaults are superseded for Python-baseline efficiency.

| Mean per complete arm | Native / Python, generated | Native / Python, captured |
| --- | --- | --- |
| Correct postings, uploads and search records | 536 / 536 | 72 / 72 |
| Completion seconds | 19.17 / 30.85 | 35.75 / 67.61 |
| Measured actor lifetime CPU seconds | 12.79 / 24.94 | 23.90 / 36.38 |
| Sampled peak working memory, MiB | 458.9 / 926.9 | 423.8 / 822.9 |
| Retained working memory, MiB | 440.6 / 863.0 | 414.4 / 799.5 |
| Modeled window EUR per 1,000 correct postings | 0.00001934 / 0.00004518 | 0.00022288 / 0.00050543 |

The captured corpus contains exact decoded response HTML for 62 JSON-LD and ten
DOM details from seven hosts and three recorded configurations. Every saved
extraction field is reproduced by both offline parsers. Keep its 1.8 MB compressed
regression corpus; consumed private captures and temporary binaries are deleted.
Configuration provenance is time-bound, not an immutable original snapshot.
Both arms use the same explicit fixture-only `skip_ssl` exception because native
HTTP embeds its production CA roots. No production TLS-cost claim follows.

Captured work starts from scheduled details. It does not capture discovery,
rendered dependencies, original full headers, original network timing or live
provider mix. Generated work independently covers discovery and rendered paths.
All worker, renderer, Redis, database, exporter, drain and fixture actors are
counted. Startup CPU is separate from modeled window cost; sampled peaks are not
absolute peaks and modeled allocation is not billed savings.

The existing workflow now runs both scenarios on native AMD64 and ARM64 runners.
New ARM ordinary-lane results are pending. Each run uses one architecture;
neither emulates production's cross-host x86 crawler / ARM renderer network.
Keep architecture and workload limits explicit before claiming production-wide
savings. Do not build a new control plane or weaken the original acceptance gate.

The observed mixed production window remains 04:35–04:44 on release 1133:
610 successful monitors, 983 successful details, 357 skipped details excluded,
3.59 mean CPU cores and 11.18 GiB working memory over 539.21 seconds. It also had
152 failed details and one failed monitor. This is actual whole-lane accounting,
not an equivalent-work Python comparison or a current-release freshness proof.

## Remaining delivery work

1. Complete both ordinary-lane scenarios on native ARM using the existing
   harness; inspect numbers, output parity, errors and cleanup. Reuse existing
   AMD64 evidence only for unchanged inputs and implementation. Account for
   actual production architecture and captured-workload limits honestly.
2. Refresh enabled-profile and queue conservation against the current canonical
   registry, including new boards. Preserve every enabled board. Continue
   normal provider recovery observations and fix demonstrated shared causes in
   batches; do not force work or invent empty successful output.
3. Complete the supported cold exercise of the changed restoration contract,
   then deploy the prepared production topology batch through fresh exact-head
   authority and required checks. Verify native actors, image contents, budgets,
   current fleet conservation and canonical storage/export effects afterwards.
4. Finish applicable natural schedule/recovery and rollback observation windows
   before removing required compatibility assets. Preserve useful offline
   workspace/labeller/oracle tooling and authenticated rollback releases. The
   full objective stays open until enabled coverage, output, publisher policy,
   database effects, efficiency, freshness and reversal are all proven.

At 13:22, Accenture and the tracked browser API-sniffer had recovered naturally
on 1137, with zero consecutive failures. Amazon's 12:57 attempt still timed out;
the tracked HTTP API-sniffer's 13:20 attempt was active. Beisen's next ordinary
retry is 14:35 and Oracle ADF's is **16:23 UTC**. Oracle's Go HTTP JSF route is
already implemented; observe its normal attempt before changing it again.
Do not infer provider causality from aggregate queue or log observations.

## Prepared retirement and cold reversal

[PR #11080](https://github.com/colophon-group/jobseek/pull/11080), candidate
**0.13.1138**, contains no new Go implementation relative to merged 1137.
It removes `worker-2`, `worker-3` and `browser-1` from production Compose and
keeps `worker-1` off-profile as a Go maintenance carrier. Forward deployment
accepts native topology; project-labelled writer discovery also quiesces removed
services. Archived legacy specs retain their bounded pool override; native-only
specs do not invent missing-image services. Offline comparisons retain a separate
legacy fixture. The 112 focused comparison/admission and affected operational
tests passed after rebase; required CI passed at the source above.

Cold reversal is qualified for 1133 through the
[supported complete exercise](https://github.com/colophon-group/jobseek/actions/runs/41167908024).
Independent readback proved the exact prior source/image/data, all four native
actors, epoch 404, 8,151 represented schedules and canonical storage/search
consistency. Repeat because this batch changes restoration and Compose contracts.

The manual `exercise_cold_rollback=true` workflow restores the actual generation
active before its attempt. Running it only after ordinary 1138 deployment would
exercise 1138-to-1138 restoration, not restoration of the pre-retirement spec.
After merge, cancel the exact automatic run only if authoritative job metadata
proves deployment mutation has not begun. Confirm terminal cancellation and
unchanged installed 1137, then run the supported main cold-exercise workflow.
Verify restored 1137 source/image, actors, schedules and effects, then run the
normal complete rollout. Never cancel an active production deployment; if it
has begun, let it finish and reassess the qualification instead of claiming an
old-spec exercise. No cancellation or exercise has been dispatched yet.

Follow [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md): immutable
images, mutation locks, complete writer quiescence, authenticated spec/data
restoration and stopped writers on failed restoration. No live rsync, mutable
deploy tags or partial restarts. Retain active generation, journal references,
explicit rollback target, five newest verified v3 generations and 48-hour staged
work grace. Before merge re-read PR state/draft, exact head/base and actual main,
required checks, reviews/comments, merge state, rules and deployment holds.
Bind normal merge to the tested head; do not bypass a hold or new decision.
