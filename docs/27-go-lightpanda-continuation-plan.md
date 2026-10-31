# Go and Lightpanda migration continuation plan

Current checkpoint: **2026-10-31 15:30 UTC**. The full migration remains active.
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

The last independent pre-exercise read at 15:06 still verifies 1137 and the
original actor budgets. Data revision:
`e137b66a962c6ecd9626e5f4c68041275b856b22`; data contract:
`128fc345c18ff833a111ff01475b455732cff85e65d60d72e1c3f3c22d2671d9`.
There are **8,163 enabled boards**, routing epoch **404**, schema **0041** and
zero active migration plans. Atomic reconciliation represents every enabled
monitor exactly once: **8,148 queued + 15 valid leases**, with zero missing,
duplicate, wrong-lane, token-presence, cache/domain or deadletter-only gaps.
Current board-key hash:
`5b078e33b6d9aaee277c0ca37435a467ce413e4c0dd3b4aff80be9a700b312db`;
monitor-ID hash:
`73b4d3b1b4be51da0a610f158a64c7e6b71bddffc5dc1a0d9d8716bb5edc9e29`.
Use current membership when company publication advances; do not reuse old
8,151-board hashes as proof of an unchanged fleet.

At 15:06, 5,341 monitors succeeded and 5,555 were checked in the preceding hour;
1,996 were due and none overdue a day. This is a bounded snapshot, not proof of
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

Latest official Linux asset hashes were rechecked at 15:20 and are unchanged. Installed and pinned:
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

**Operator-approved Python boundary, October 31:** port performance-sensitive
crawler paths to Go. Keep Lightpanda host firewall/containment, deployment
verification and other host infrastructure tooling in Python. Those protected
operator tools may touch live host state through their existing authorized
workflows; they do not execute crawler extraction, enrichment or per-task
fallbacks. Their retained language is not an outstanding migration requirement.
Keep useful isolated workspace, labeller, test-oracle and offline Python too.
Do not expand this work into a host security/control-plane rewrite.

## Verified comparisons and their limits

[PR #11080](https://github.com/colophon-group/jobseek/pull/11080) passed Required
CI and Crawler Deploy Gate at exact head
`b0a04f2e353668da020066f20362401582af415d` and is merged. All four complete-lane
scenarios finished six alternating arms with identical canonical output,
uploaded descriptions, matching search records and successful cleanup.
The control explicitly selects Python JSON-LD/DOM extraction and enrichment;
Go reaper/exporter/drain remain common measured actors. Earlier controls that
inherited Go extraction defaults are superseded for Python-baseline efficiency.

| Mean per complete arm, native / Python | AMD64 generated 536 | AMD64 captured 72 | ARM64 generated 536 | ARM64 captured 72 |
| --- | --- | --- | --- | --- |
| Completion seconds | 19.0006 / 32.1777 | 34.2380 / 65.7946 | 20.4292 / 32.1863 | 34.0391 / 63.9817 |
| Actor lifetime CPU seconds | 15.9133 / 30.8827 | 18.6312 / 28.7112 | 13.9656 / 26.9371 | 15.5211 / 23.0003 |
| Sampled peak working memory, MiB | 462.0156 / 913.9740 | 427.7292 / 816.3555 | 462.1641 / 924.7917 | 421.5977 / 820.4427 |
| Retained working memory, MiB | 443.0299 / 853.1875 | 415.3646 / 798.9401 | 447.9128 / 867.4310 | 416.0286 / 804.2305 |
| Modeled EUR per 1,000 correct postings | 0.0000195714 / 0.0000457436 | 0.0001929646 / 0.0004733595 | 0.0000201087 / 0.0000464483 | 0.0001853936 / 0.0004682200 |

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

The existing workflow runs both scenarios on native AMD64 and ARM64 runners.
Each run uses one architecture;
neither emulates production's cross-host x86 crawler / ARM renderer network.
Keep architecture and workload limits explicit before claiming production-wide
savings. Do not build a new control plane or weaken the original acceptance gate.

The observed mixed production window remains 04:35–04:44 on release 1133:
610 successful monitors, 983 successful details, 357 skipped details excluded,
3.59 mean CPU cores and 11.18 GiB working memory over 539.21 seconds. It also had
152 failed details and one failed monitor. This is actual whole-lane accounting,
not an equivalent-work Python comparison or a current-release freshness proof.

## Remaining delivery work

1. Finish the existing supported cold exercise, independently verify the actual
   previous source/image/data/spec restoration, then run the normal complete
   rollout. Verify native actors, budgets, canonical effects and current fleet
   schedule conservation. Do not dispatch a duplicate exercise.
2. Publish the shared streaming-monitor inactivity fix below through fresh
   required CI and deploy authority. Observe normal provider recovery; batch
   demonstrated shared causes and preserve every enabled board.
3. Finish applicable natural schedule/recovery and rollback observation windows
   before removing required compatibility assets. Preserve useful offline
   workspace/labeller/oracle tooling and authenticated rollback releases. The
   full objective stays open until enabled coverage, output, publisher policy,
   database effects, efficiency, freshness and reversal are proven within their
   stated observation scopes. Approved host/control Python does not block it.

By 13:46, Accenture and both tracked API-sniffer routes recovered naturally on
1137 with zero consecutive failures. Amazon's 12:57 attempt still timed out
after seeing 15,584 postings; this motivated the shared monitor progress fix.
Beisen's next ordinary retry was 14:35 and Oracle ADF's is **16:23 UTC**. Oracle's
Go HTTP JSF route is already implemented; observe its normal attempt before
changing it again. Do not force work or repair schedules to obtain evidence.
Do not infer provider causality from aggregate queue or log observations.

## Prepared retirement and cold reversal

[PR #11080](https://github.com/colophon-group/jobseek/pull/11080), candidate
**0.13.1138**, merged as
`dbe13c0fb452f7b72edced2e8dac174084a9cdb3`. It contains no new Go
implementation relative to deployed 1137.
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
The exact automatic run **41213054993** was cancelled only after authoritative
metadata proved no deployment job existed; terminal cancellation and unchanged
1137 were independently verified. The supported
[cold exercise 41213487268](https://github.com/colophon-group/jobseek/actions/runs/41213487268)
is now active at merged source `dbe13c0fb452f7b72edced2e8dac174084a9cdb3`.
Native build job 124573584207 passed, with candidate image
`sha256:47be5a6acf28b62b95c0f0bce28253d768b706647a809785e83645437a41e7ec`;
deployment job 124575989514 is executing the supported host workflow. Promotion
must be skipped. The 14 prior deployment-spec presence/hash fingerprints were
captured before mutation. No normal 1138 rollout has been dispatched.

Follow this existing run to terminal state. Independently verify restored source,
image, data, deployment specs, actors, budgets and complete schedule membership,
then run the normal complete main rollout. Never cancel an active production
deployment or substitute manual state repair for supported restoration.

## Shared monitor progress follow-up

Candidate **0.13.1139** fixes a shared timeout mismatch affecting large streaming
monitors. Python renews its inactivity budget between streamed batches; Go used
one fixed ten-minute wall-clock deadline even when batches kept committing.
Renew only after authoritative batch writes and detail publication succeed.
Heartbeats and upstream responses cannot keep stalled work alive. Keep the
existing inactivity budget, explicit operator absolute task cap, detail deadline,
Njoyn provider cap, lease/fence authority, cancellation grace and all capacity
limits. Parent cancellation must never grant fresh settlement authority.

Local verification on frozen source: 21 focused unit and real PostgreSQL/Redis
race cases pass; the complete module reports 4,246 passes and 35 existing skips.
Its only six failures came from a temporary local DOC tool missing character
maps under the extractor's restricted environment. All 38 affected document
cases pass with that fixture setup corrected; `go vet` passes. No production
source was changed to fix the local tool. Rebase preserved all seven verified
source hashes. Required CI and deployment qualification remain pending.

Follow [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md): immutable
images, mutation locks, complete writer quiescence, authenticated spec/data
restoration and stopped writers on failed restoration. No live rsync, mutable
deploy tags or partial restarts. Retain active generation, journal references,
explicit rollback target, five newest verified v3 generations and 48-hour staged
work grace. Before merge re-read PR state/draft, exact head/base and actual main,
required checks, reviews/comments, merge state, rules and deployment holds.
Bind normal merge to the tested head; do not bypass a hold or new decision.
