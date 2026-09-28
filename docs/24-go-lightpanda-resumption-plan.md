# Go + Lightpanda migration: resumption plan

Status: ongoing migration; latest deployed checkpoint, 2026-09-28. The initial review used
`origin/main` `6abfa52e1b061f2898a46355c48105accd1c01f9`; the B0 producer
implementation merged as `dcdc407ba836d75ec93e7416faa5f9fdabd41001`.
The fixture admission gate has passed; [#8648](https://github.com/colophon-group/jobseek/issues/8648)
tracks current production c1 admission evidence.

## Production checkpoint: 2026-09-28 — Go rendered DOM implementation, cdom not admitted

[PR #10157](https://github.com/colophon-group/jobseek/pull/10157) merged as
**`fb2b1c865e90ccf67379aa1f303e2b5f5e8f95c0`**, crawler **v0.13.891**.
Required CI, Crawler Deploy Gate, installed runtime contracts, native Lightpanda
on amd64/arm64 and the ARM64 service deployment contract passed at ready head
`a57ba27cfe3dcb5219576726c5f8bea90b7e1810`. The crawler
[deployment 36431451418](https://github.com/colophon-group/jobseek/actions/runs/36431451418)
reached terminal overall success, including promotion. The host active release
confirms that full crawler revision.

Go now implements all four navigation waits, bounded current-document readiness
fallback without another navigation, one conditional same-page transport retry
after 500 ms, and one fresh-context challenge retry under the renewed original
queue lease and write fence. The challenge classifier uses the installed Go
parser and validates canonical protobuf, HTML manifests, chunks, hashes and
UTF-8 before classifying. Typed errors, HTTP failures, gone pages, cancellation
and lost authority cannot trigger a fresh retry. Native tests cover recovered
and exhausted TCP resets with exactly two requests. The installed extractor
preserves 500 frozen field cases and 83 Python-derived gone/challenge cases;
the immutable runtime-v1 baseline remains unchanged.

[Renderer deployment 36431710458](https://github.com/colophon-group/jobseek/actions/runs/36431710458)
reached terminal overall success at the same source revision. Its immutable image
is `ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:72b73991cc4a6820263970ccf07367b5d9193eb37cb9d742701fb78ea0104a21`.
The installed ARM64 Lightpanda still matches the official **September 28 nightly**
checksum `e5e3b57fb1c99325c1b66e5f1e25d02199f21d116126a74296578bcc0ae9cd8f`.
Upstream asset IDs/digests were rechecked; source, architecture and release labels
match the deployed tuple.

**cdom admission was refused.** The DB baseline contains 39 active rows with
`next_scrape_at`: Browser Use 5, Bunq 15, Algorized 19. This was not proof of 39
transferable Redis schedules. Supported c1 rollback retired epoch **117**,
restored all five schedules with zero drops/fences, and cleared the exact 25
selectors. After both deployments succeeded, selectors were staged at the new
crawler revision. The supported cdom plan at epoch **118** then rejected
`legacy scrape hash disagrees with authoritative PostgreSQL` before task transfer.
Its containment stopped the crawler lane. Supported `activate c1` restored
service at epoch **119**, selected/activated five schedules, and returned
accepted/audit_ok conservation with five ready and zero inflight. All configured
worker/browser/drain/producer/executor/claimant/Redis services are healthy.

Two subsequent **natural c1 Go render/commit cycles** correlate with advanced
PostgreSQL scrape timestamps. Their identities, active flags, titles, description
hashes and zero failure counts match the pre-rollout readback. All five c1 jobs
retain zero failures. These establish natural operation of the new renderer;
**they do not establish rendered DOM admission**. No publisher request, due time
or queue priority was forced. C2 Kandou remains dark.

A warm read-only diagnostic after c1 restoration found 17 DOM ready hashes with
empty description hashes despite populated PostgreSQL hashes, and another 17
DB-schedulable DOM rows without matching hashes/memberships in that snapshot.
Repeat an exact cold census before a repair. Reconcile the existing queue
publisher/retention semantics while preserving hash/score authority, exhausted
and dead work, and request policy. Do not manufacture schedules, reset failures,
relax the transfer proof, or omit enabled boards to make cdom activatable.

The independent ARM64 [whole-lane run 36429730454](https://github.com/colophon-group/jobseek/actions/runs/36429730454)
passed 16 arms, with 5.47–14.58x density ratios. It is a **synthetic fixture**;
actual-workload whole-lane production CPU/RAM/density/cost remains unfinished.
Python and Chromium still own production work. The full migration remains active;
[#7966](https://github.com/colophon-group/jobseek/issues/7966) is owner-closed and
was not reopened. Remaining native profiles, Go runtime ownership and final
cutover/reversal remain required.

Before any further crawler deploy or selector mutation, use supported
`rollback c1`, then clear all **25** selectors under
`/run/lock/jobseek-crawler-mutation.lock` with the unchanged
`scripts/migration-jsonld-selectors.py`, full promoted crawler revision
**`fb2b1c865e90ccf67379aa1f303e2b5f5e8f95c0`**, and exact Kandou URL
`https://kandou.bamboohr.com/careers/310`. Helper SHA-256 is
`8232a219cc9bc4236a9aab6d77ee85321f257afd7d577c365c7b3ba636ac9743`.
Stage only after terminal overall next-deployment success at its promoted full
revision. Reactivate c1 through the supported wrapper; cdom requires the schedule
repair and renewed admission proof. **This supersedes every older revision below.**

See [sanitized production evidence](evidence/lightpanda-rendered-dom-production-2026-09-28.json).
Protected logs, natural result/readbacks and fixture artifacts remain in
`/Users/Viktor/.codex/migration-evidence/lightpanda-dom/2026-09-28/`
(directory 0700, files 0600).

## Production checkpoint: 2026-09-28 — current Lightpanda nightly

[PR #10156](https://github.com/colophon-group/jobseek/pull/10156) replaced
Lightpanda 0.4.0 with the official nightly assets updated on **2026-09-28**.
The nightly tag is mutable; its old tag creation time/commit does not identify
these binaries. Downloads bind immutable asset IDs and fail closed on SHA-256
mismatch. The [release snapshot](../pilots/go-lightpanda/lightpanda-release.json)
is included in the image.

- Linux amd64 asset **594332787**, updated 02:56:04 UTC:
  `e7dfca7686ad4ca5831b5cd741684b2dde6579c9f6046a848206cf5ad39efd3f`.
- Linux arm64 asset **594332898**, updated 02:56:09 UTC:
  `e5e3b57fb1c99325c1b66e5f1e25d02199f21d116126a74296578bcc0ae9cd8f`.

Required CI and Crawler Deploy Gate passed at exact head
`9f888a5603d392efb595f1071effcb8b10547e40`. The real native browser, egress,
cleanup and child-isolation contracts passed on **both architectures** in
[run 36421148740](https://github.com/colophon-group/jobseek/actions/runs/36421148740).
The ARM64 service deployment contract also passed. Density smoke results are
fixtures; they do not prove production whole-lane savings.

The renderer source is **`ffb8d6902260eee91728e5121ca016e42275c6e1`**.
[Deployment 36422355712](https://github.com/colophon-group/jobseek/actions/runs/36422355712)
reached terminal overall success. Installed production ARM64 bytes match the
upstream checksum above. The immutable image is
`ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:5b43d7017393a70f0531d494bf183aa8c4a259eb38119255aa9aa0cd3c1d6c55`.
The installed release metadata and source tuple match that deployment.

Supported cold rollback retired c1 at **epoch 115**, restored all five schedules,
and left zero terminal drops and write fences. Supported reactivation completed
at **epoch 116** with five ready, zero inflight and accepted/audit_ok conservation.
All configured worker/browser/drain/producer/executor/claimant/Redis services
are healthy. No new boards were admitted and no publisher request or schedule
was forced. The crawler remains **v0.13.890** at
**`e62cec0d4349b72c39071bf3d0834456cdfab728`**; all **25** staged selectors remain
at that crawler revision. This renderer-only rollout did not mutate selectors.
Use that promoted crawler revision and the unchanged selector helper for the
next supported cold crawler rollout, as described immediately below.

See the [sanitized production record](evidence/lightpanda-nightly-production-2026-09-28.json).
Private rollback, activation and conservation records are retained at
`/Users/Viktor/.codex/migration-evidence/lightpanda-latest/2026-09-28/`
(directory 0700; files 0600).

The rendered DOM implementation subsequently merged and deployed in
[PR #10157](https://github.com/colophon-group/jobseek/pull/10157). Its current
production tuple, retry proof, refused cdom admission and restored c1 state are
recorded in the newer checkpoint above. The full migration remains active.

## Production checkpoint: 2026-09-28 — Go DOM direct HTTP v0.13.890

[PR #10154](https://github.com/colophon-group/jobseek/pull/10154) merged as
**`e62cec0d4349b72c39071bf3d0834456cdfab728`** after Required CI, installed-image
contracts and Crawler Deploy Gate passed at exact head
`e2b2d7f607051b29db8e1430d21443b2f3ce4d95`.
[Deployment 36416267049](https://github.com/colophon-group/jobseek/actions/runs/36416267049)
reached terminal overall success, including promotion. The host's active-release
snapshot confirms that full revision.

**Go now owns admitted direct DOM origin reads by default**, through the
installed `dom-detail-fetch` binary. The 11:08 UTC protected inventory admits
**549 of 758 primary DOM board URLs/configurations**, with zero admission
errors; 209 rendered/proxied/insecure configurations retain their existing
transport. Actual posting fetch URLs and effective verified-direct caller
clients are checked at runtime. Metadata-based insecure TLS, proxy routing and
custom/logging clients retain their configured behavior. Every enabled board
remains configured and available.

The native transport preserves raw bytes, content type and final URL;
configured status retries, Avature 406 retries, cookie-aware same-origin
redirects and public request headers preserve their contracts. Public headers
use five redirects, no forwarded cookies and no status retry; other routes
retain the 20-redirect bound. Public DNS/IP validation and TDM header/meta checks
precede extraction. Bodies are bounded at 16 MiB and native tasks at ten minutes.
Python still owns configured decoding, gone/challenge classification, PDF/DOCX
conversion, linked fetches and worker/persistence glue. The installed Go parser
owns extraction. Metrics identify `go-dom-http` at stage `fetch`; they do not
label the whole scrape Go. Rendered DOM still uses Playwright/Chromium and B0
admission remains JSON-LD-only.

Focused checks passed **263 Python tests**, native race/vet/tidy for both modules,
**500 frozen parser cases** and **four installed HTTP private-target rejection
cases**. These establish contract admission, not production resource savings.

At **11:46 UTC**, eight exact natural parse inputs were retained securely before
worker recreation. All ten fields match canonical Python and each natural Go
completion hash. One populated **EHC** case also binds its exact UTF-8 HTML hash
to the native HTTP raw-body hash: status 200, one request/response, 345,696 raw
bytes. Its canonical enriched/normalized 5,173-byte description, title policy,
employment type, posting/description hashes and fresh scrape timestamp match
PostgreSQL; R2 upload is complete, the row is active and scrape failures are zero.
A populated rendered **MUTB** case has the same parser/DB proof while retaining
browser transport. Six rendered Bajaj inputs are empty in both parsers and have
existing failure counts of two or three; one has exhausted its normal retry
schedule. They did not use Go HTTP and are not populated-output proof.
No publisher request, schedule or queue priority was forced.
See the [sanitized release evidence](evidence/go-dom-http-production-2026-09-28.json).

Supported rollback retired c1 at **epoch 113**, restored all five schedules and
left zero drops or write fences. All 25 selectors cleared at the preceding
promoted runtime. After overall deployment success they were staged at
`e62cec0d4349b72c39071bf3d0834456cdfab728`; supported activation completed at
**epoch 114**. Conservation is accepted/audit_ok, with five ready and zero
inflight. Configured worker/browser/drain/producer/executor/claimant/Redis health
checks pass; c2 was not activated. At **11:50 UTC**, nine additional natural Go
HTTP fetches had completed successfully with no error outcome counter observed.
Those additional logs are not independent DB parity proof. The Typesense startup
snapshot shows 273 successful documents, zero document/CDC flush errors, zero
lag and healthy status; it is not fleet-wide zero-error proof.

Before any further crawler deploy or selector mutation, finish supported
`rollback c1`, then clear all **25** selectors under
`/run/lock/jobseek-crawler-mutation.lock` using
[`scripts/migration-jsonld-selectors.py`](../scripts/migration-jsonld-selectors.py)
in `clear` mode at full promoted revision
**`e62cec0d4349b72c39071bf3d0834456cdfab728`**, with exact Kandou URL
`https://kandou.bamboohr.com/careers/310`. Helper SHA-256 remains
`8232a219cc9bc4236a9aab6d77ee85321f257afd7d577c365c7b3ba636ac9743`.
After terminal overall next-deployment success, stage only at its promoted full
revision and reactivate c1. **This procedure supersedes every older revision
below.** Restore v0.13.889 through the same supported cold procedure to reverse
this transport slice. `DOM_GO_HTTP_ENABLED=0` is the configuration reversal
contract; never edit the host environment by hand.

Protected exact inputs, comparators, DB readback and operational logs remain at
`/Users/Viktor/.codex/migration-evidence/go-dom-http/2026-09-28/`
(directory 0700; files 0600). Inventory SHA-256 is
`fa0e92cc60c79be2ddec2c8e42367fda47c53c52903de2bee374a755fec96c31`.
Keep those bytes private and durable; do not reconstruct inputs or refetch
origins. **This deployed checkpoint is complete; the full migration goal remains
active.** Next implement rendered-DOM Lightpanda admission and remaining Go
profiles/runtime stages. Paired actual-workload whole-lane CPU/RAM/density/cost
proof and final cutover/reversal remain unfinished. Issue #7966 stays owner-closed
and its full gate remains the completion criteria. Do not claim full transition
while Python, Playwright or Chromium own production work.

## Production checkpoint: 2026-09-28 — shared DOM extraction v0.13.889

[PR #10146](https://github.com/colophon-group/jobseek/pull/10146) migrated the
shared DOM parser to Go and merged as `bdd21ede7882e33049500649dc95d274decccf9d`.
A concurrent runtime release consumed version 888; VERSION-only
[PR #10149](https://github.com/colophon-group/jobseek/pull/10149) assigned 889
and merged as **`abb96a57dd3da5eed42a31d52c97ffdf7770ba6c`**.
[Deployment 36409703759](https://github.com/colophon-group/jobseek/actions/runs/36409703759)
reached terminal overall success. The queued duplicate-version deployment was
cancelled before execution. Required CI, installed-image parity and the deploy
gate passed for the implementation; the corrected release's required checks
and installed-image contracts also passed.

**Go now owns shared DOM extraction by default.** Scope selection, flattening,
seek/walk, regex/date transforms, defaults and all ten `JobContent` fields run
in the installed `dom-detail-parse` binary. The inventory contains **758 primary
DOM boards / 684 distinct configurations**, including 166 rendered and 45
proxied configurations. Preview and linked-HTML parsing use the same binary.
Normal HTTP/browser requests, policy, retries, linked fetches and PDF/DOCX
conversion remain with their existing callers. Rendered DOM transport still
uses Python/Playwright/Chromium. B0 admission remains JSON-LD-only; this parser
release does not admit rendered DOM into Lightpanda or establish full Go DOM
scrape ownership.

The bounded offline checks passed **500 frozen Python cases**, native race/vet,
247 focused Python tests, all 571 production regex patterns across 4,568 probes,
and all 54 distinct production CSS scopes. These checks established admission
before deployment; they are not production resource evidence.

At **10:41 UTC**, 20 exact natural parse requests were securely retained before
worker recreation. Every input matches canonical Python across all ten fields
and matches the natural Go completion hash. The retained logs contain 54 natural
Go extraction completions. No publisher request or schedule was forced.
**Eighteen populated cases** match canonical normalized/enriched description
bytes in PostgreSQL, the applicable title-write policy, and employment type;
all are active, uploaded to R2 and have zero scrape failures. Ten cases have
populated title writes; description-only enrichment retains existing titles.
All 20 database scrape timestamps are at or after their matching completion.
Sixteen populated cases match current byte hashes. Pilatus and CACEIS retain
older hashes with unchanged exact HTML under the existing byte-equality UPSERT
policy; each scalar posting hash equals its stored description-row hash.
The two empty cases match both parsers and retain active rows with normal
transient failure counts of two and one. They are not populated-output proof.
See the [sanitized release evidence](evidence/go-dom-detail-production-2026-09-28.json).

Supported rollback retired c1 at **epoch 111**, restored all five schedules and
left zero drops or write fences. All 25 selectors cleared at the preceding
promoted runtime. After terminal deployment success, the unchanged overlay was
staged at `abb96a57dd3da5eed42a31d52c97ffdf7770ba6c` and c1 reactivated at
**epoch 112**. The conservation audit is accepted/audit_ok with five ready and
zero inflight. Configured worker/browser/drain/producer/executor/claimant/Redis
health checks pass; c2 remains dark. The 10:46 UTC Typesense snapshot reports
2,878 successful documents, one document error, zero CDC flush errors, zero
export lag and healthy status. The document error is not attributed to DOM
parsing; this snapshot does not establish zero index errors.

Before any further crawler deploy or selector mutation, finish supported
`rollback c1`, then clear all **25** selectors under
`/run/lock/jobseek-crawler-mutation.lock` using
[`scripts/migration-jsonld-selectors.py`](../scripts/migration-jsonld-selectors.py)
in `clear` mode, full promoted revision
**`abb96a57dd3da5eed42a31d52c97ffdf7770ba6c`**, and exact Kandou URL
`https://kandou.bamboohr.com/careers/310`. Helper SHA-256 remains
`8232a219cc9bc4236a9aab6d77ee85321f257afd7d577c365c7b3ba636ac9743`.
After terminal next-deployment success, stage only at its promoted full revision
and reactivate c1. **These instructions supersede every older revision below.**
Reverse extraction with `DOM_GO_PARSE_ENABLED=0` through the supported cold
procedure or restore the preceding release. Do not manually edit `.env`.

Private exact stdin bytes, captures, comparators, database readback and release
logs are retained at
`/Users/Viktor/.codex/migration-evidence/go-dom-detail/2026-09-28/`
(directory 0700; files 0600). `compare-capture.py` compares the exact bytes with
both parsers offline and `compare-database.py` applies canonical enrichment,
normalization and title policy to the protected readback. Keep the original
bytes private and durable; do not reconstruct inputs or refetch origins.

This is a historical deployed extraction checkpoint. **The full migration goal
remains active.** Go DOM HTTP transport is now deployed in the checkpoint above;
next implement rendered-DOM Lightpanda admission, remaining profiles and
Python-owned runtime stages. Paired
same-actual-workload whole-lane CPU/RAM/density/cost proof and final
cutover/reversal remain unfinished. Do not claim completion while Python,
Playwright or Chromium still own production work; issue #7966 remains
owner-closed and its full gate remains the completion criteria.

## Production checkpoint: 2026-09-28 — SmartRecruiters broad details v0.13.886

PR #10141 merged as `be97fa7a2c5edf5afe7c604b4a5a049cf2d17aca`.
[Deployment 36398199996](https://github.com/colophon-group/jobseek/actions/runs/36398199996)
reached terminal overall success after Required CI, installed-image parity and
the crawler deploy gate passed at exact head
`06329780dd436cec8bb015a223e28620e2659b85`. The browser image and lifecycle
gate also passed. The default-100 SmartRecruiters detail route is deployed.
No enabled boards or active URLs were removed to make the route pass.

The 08:50 UTC read-only census selects Go for **all 121 SmartRecruiters boards
with active URLs**, up from five exact selected boards. Five other enabled
SmartRecruiters boards have no active URLs; all 126 resolved configurations
remain enabled and default/null. The current active inventory is 109,873 jobs.
The earlier exact offline corpus proved admission and canonical Python identity
for all 109,792 then-active URLs; the later inventory is a separate snapshot.

Using **one minimum active URL per resolved board**, the whole primary-detail
sample changed from **838 Go / 2,202 Python** to **955 Go / 2,085 Python** across
the same 3,040 resolved boards, with 4,839 rich-monitor skips and no route errors.
SmartRecruiters accounts for 116 newly selected boards; one JSON-LD board also
gained an admitted representative URL. Empty boards and provided B0 runtimes
limit this sampling method, so these counts must not be substituted for the
earlier configuration-based census or claimed as complete per-URL coverage.

Supported rollback retired c1 at **epoch 105**, restored all five schedules,
and left zero dropped tasks or write fences. All 25 selectors cleared under
the mutation lock. After terminal deployment success, the unchanged 25-selector
overlay was staged at the promoted revision and c1 reactivated at **epoch 106**.
The current conservation audit is accepted/audit_ok with five ready, zero
inflight and no dead records. Configured worker/browser/drain/producer/executor/
claimant/Redis health checks pass; c2 remains dark. Typesense reports 1,927
successful documents, zero errors, a 15-row lag and healthy status in the recorded
startup observation.

**Natural SmartRecruiters detail proof remains pending.** The 08:55 UTC sample
contains zero new detail completion records and zero retained API samples.
The normal recurring domain has 229,641 queued entries; its subsequent tier-2
rank was 1,337 among 1,377 due domains. No due dates, queue priority or origin
requests were forced. These deployment and routing results establish the
durable code checkpoint; they do not establish live rich-field/database parity
for this detail slice or a paired production resource comparison. See the
[sanitized release evidence](evidence/go-smartrecruiters-detail-production-2026-09-28.json).

Before any further crawler deploy or selector mutation, finish supported
`rollback c1`, then clear all **25** selectors under
`/run/lock/jobseek-crawler-mutation.lock` with
[`scripts/migration-jsonld-selectors.py`](../scripts/migration-jsonld-selectors.py)
in `clear` mode, full promoted revision
`be97fa7a2c5edf5afe7c604b4a5a049cf2d17aca`, and exact Kandou URL
`https://kandou.bamboohr.com/careers/310`. Helper SHA-256 remains
`8232a219cc9bc4236a9aab6d77ee85321f257afd7d577c365c7b3ba636ac9743`.
After terminal next-deployment success, stage only at its promoted full revision
and reactivate c1. This supersedes the earlier JSON-LD release instructions.
Broad-detail reversal uses `SMARTRECRUITERS_GO_DETAIL_PERCENT=0` through the
supported cold procedure; exact detail selectors retain precedence and must
also be cleared for complete reversal, or restore the preceding runtime release.

Private inventories, routing readbacks, normal queue position, B0 audit and
release logs are retained at
`/Users/Viktor/.codex/migration-evidence/go-smartrecruiters-detail/2026-09-28/`
(directory 0700; snapshots 0600). The protected `collect-production.py` reads
existing worker captures/completions and internal metrics without publisher
traffic. Once passive API samples arrive, securely copy the original envelopes
mode 0600 and use the checked-in offline comparator below against the natural
completion hash, followed by PostgreSQL description/content/failure readback.
Continue implementation while the normal queue is pending. Full zero-Python/
Playwright/Chromium ownership, final reversal and paired whole-lane resource
proof remain unfinished; overall migration completion is not claimed.

## Implementation checkpoint: SmartRecruiters broad details v0.13.886

The existing native SmartRecruiters detail extractor now has a default-100
`SMARTRECRUITERS_GO_DETAIL_PERCENT` route for unchanged direct configurations.
The protected read-only inventory contains **126 primary detail configurations**,
all default/null, with **109,792 active URLs** on 121 boards. Offline admission
accepts every URL and preserves the canonical Python company/posting identity;
zero URLs were omitted or rewritten. Inventory SHA-256 is
`a91b169264fa19fedc43042f7b73e8869b5fb09c2aea7197274af3e1ab0ea8df`.
The five exact detail selectors retain precedence. Provided B0 runtimes still
own their selected work, while idle browser handles no longer reject a direct
Go request within mixed batches. Non-200 detail responses preserve Python's
empty-content behavior and single-request policy.

Only the existing exact detail selectors retain passive Go API samples:
four exclusive mode-0600 response envelopes per board/worker, after body and
publisher-policy checks, with no refetch or implicit percentage-route capture.
[`scripts/compare-smartrecruiters-detail-capture.py`](../scripts/compare-smartrecruiters-detail-capture.py)
compares the retained response with both parsers and the natural completion
hash entirely offline. Preserve private captures outside temporary directories.
Runtime tests cover route reversal, provided-runtime precedence, ordinary and
oneclick identity, ambiguous/encoded URL rejection, idle browser handling,
single-request capture, policy denial and four-sample bounds; the existing
86 installed-image fixture cases remain the runtime deployment gate.

Deployment and routing readback are recorded in the production checkpoint above;
natural Go detail/database proof remains pending. Use its current **25-selector**
supported cold procedure before any further deployment. Reversal sets the detail percentage
to zero and removes exact detail selectors through that procedure, or restores
the preceding runtime release. This slice retains Python enrichment, persistence
and scheduling ownership; it does not prove overall migration completion or
paired production resource savings.

## Production checkpoint: 2026-09-28 — shared JSON-LD details v0.13.885

PR #10122 merged as `a91adb088c9f2e5c6a0a2e29c56a03e7add220db`.
[Deployment 36370674696](https://github.com/colophon-group/jobseek/actions/runs/36370674696)
succeeded after Required CI, installed-image parity and the exact-head crawler
deploy gate passed. Go now selects **700 of 802 primary JSON-LD configurations**.
The protected predeploy inventory admitted all **312,629 active URLs** on those
configurations. No enabled board was removed. The postactivation primary-detail
census is **974 Go / 2,066 Python** of 3,040 resolved boards, with 4,839 rich-monitor
skips and zero route errors across 7,879 enabled boards. Provided B0 runtimes are
outside that census. Six of JOIN's eight JSON-LD configurations now select Go.

At approximately 07:45 UTC, retained logs contained **1,188 natural Go JSON-LD
completions**. Five passive envelopes from Gupy, iCIMS and NTT DATA match the
Python parser across all ten fields and match the natural Go completion hashes.
The two NTT DATA cases also prove the configured CSS description override.
Four populated cases have exact canonical normalized description bytes,
titles and employment types in PostgreSQL, matching description/R2 hashes,
uploaded R2 descriptions and zero scrape failures. One iCIMS envelope produces
the same empty extraction in both parsers; its existing active row is retained.
It is an empty-result behavior check, not a populated output proof.

The subsequent runtime counters report **1,237 successes / 250 errors**;
success counters include empty extractions. A bounded read-only attribution of
those error records finds **236 HTTP 410, 12 HTTP 404 and two transport failures**.
The sampled Kuehne+Nagel HTTP 410 correctly tombstoned its posting. The two
Swatch transport failures remain subject to normal transient handling; no
origin replay or forced retry was performed. Typesense reports 27,930 successful
exports, zero errors, zero lag and healthy status. Configured service health
checks pass. These snapshots establish the recorded cases, not fleet-wide
field parity or a paired resource comparison.

Supported cold rollback retired c1 at **epoch 103**, restored all five schedules
and left zero dropped tasks or write fences. The old 24 selectors cleared under
the host mutation lock. After terminal deployment success, **25 selectors** were
staged against the promoted revision and c1 reactivated at **epoch 104**. The
current audit is accepted/audit_ok with five ready, zero inflight and no dead
records; c2 remains dark. One previously due schedule advanced during the cold
interval to 2026-09-29 02:46:23 UTC. Queue state alone does not identify that
cycle's engine owner. See the
[sanitized evidence](evidence/go-jsonld-detail-production-2026-09-28.json).

Before any further crawler deploy or selector mutation, complete supported
`rollback c1`, then clear the **25** selectors under
`/run/lock/jobseek-crawler-mutation.lock` using the checked-in
[`scripts/migration-jsonld-selectors.py`](../scripts/migration-jsonld-selectors.py)
in `clear` mode against full revision
`a91adb088c9f2e5c6a0a2e29c56a03e7add220db` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. The exact helper SHA-256 is
`8232a219cc9bc4236a9aab6d77ee85321f257afd7d577c365c7b3ba636ac9743`.
After the next deployment reaches terminal success, stage only at its promoted
revision and reactivate c1. These instructions supersede the older 24-selector
checkpoint below. Restore the preceding release through the supported cold
procedure to reverse this complete JSON-LD slice, including B0 parser ownership.

The retained bytes and current configuration/posting readbacks are preserved in
`/Users/Viktor/.codex/migration-evidence/go-jsonld/2026-09-28/`, directory mode
0700 and capture/snapshot modes 0600. The original temporary predeploy snapshot
has expired locally; its historical inventory and digest remain recorded.
Use [`scripts/compare-jsonld-capture.py`](../scripts/compare-jsonld-capture.py)
with the retained capture, native `--parse` binary, `--configs`, `--baseline`
and `--completion-sha256` arguments to reproduce the comparison without HTTP.
Keep future private evidence outside temporary directories.

Continue implementation: **102 JSON-LD transport configurations** remain Python
(79 rendered, including 13 also proxied; 20 other proxied; two skip-SSL; one
Workday recovery). The next shared DOM inventory has **758 primary detail
boards / 684 distinct configurations**, including 166 rendered and 45 proxied.
Python worker/writer ownership, other monitor/detail profiles, Playwright and
Chromium retirement, whole-lane same-actual-workload CPU/RAM/density/cost proof,
and final cutover/reversal remain unfinished. The full migration goal is active;
the owner-closed #7966 is not evidence that its completion requirements passed.

## Implementation PR #10122: shared JSON-LD details v0.13.885

The isolated `fix-crawler/go-jsonld-detail` worktree implements the shared Go
parser, direct HTTP transport and parser ownership for validated Lightpanda HTML.
The frozen oracle covers 167 distinct parser inputs and nine CSS-description
cases; native transport tests preserve bounded 403/Avature 406 retries,
content/iframe recovery, typed terminal status and publisher policy. Local
native race/vet checks and 62 bridge/Lightpanda/JOIN runtime tests pass.
The current registry admits **700 of 802** primary JSON-LD configs and all
**312,629 active URLs** on those admitted configurations, including 66 BDO and
Richemont fragment URLs. The protected predeploy snapshot contains 400,756
active JSON-LD jobs across all 802 configs. Remaining
transport obligations are 79 rendered (13 also proxied), 20 other proxied, two
skip-SSL and one configured Workday recovery. No enabled board is removed.
This historical implementation evidence is supplemented by the production
checkpoint above. Actual-workload whole-lane measurements remain pending.
Follow the latest production cold rollback protocol above before any deploy
or selector mutation. Overall migration completion is unproven.

## Production checkpoint: 2026-09-28 — JOIN details v0.13.884

PR #10120 merged as `cc1e273aa35b3f72b53eb82930fc4e39c9380e51`.
[Deployment 36365143439](https://github.com/colophon-group/jobseek/actions/runs/36365143439)
succeeded. All **268 enabled JOIN Next.js detail configurations** now select
Go: 265 mapped boards across four forms and three no-field boards retaining
empty extraction before HTTP. The actual URL admission check selects all
**4,072 active postings** on those boards with zero exclusions. Eight JOIN
JSON-LD configurations remain separate migration obligations. No board was
disabled. The live primary-detail census is **274 Go / 2,766 Python** of 3,040
resolved boards, with 4,839 rich-monitor skips and zero route errors across
7,879 enabled boards. This census excludes provided B0 runtimes.

Required CI, installed-image parity and the exact-head deploy gate passed.
The installed offline JOIN oracle includes 52 monitor and 40 detail cases;
14 native policy fixtures also pass. The shared idle Playwright handle from
mixed domain batches is ignored by the direct native config, preserving its
HTTP ownership. The initial readback retains 4,169 active JOIN postings with
no increased scrape failures. Typesense reports 182 successful exports, zero
errors, zero lag and healthy status. All configured service health checks pass.

Supported cold rollback retired c1 epoch 100 at **101**, restored all five
schedules and left zero terminal drops or write fences. The **24 selectors**
cleared under the host mutation lock, then restaged at the promoted revision.
C1 reactivated at **epoch 102**, accepted/audit_ok with five ready, zero inflight
and no dead records. All five retained due times are unchanged; c2 remains dark.
See [sanitized production evidence](evidence/go-join-detail-production-2026-09-28.json).

**Natural Go detail output and same-byte comparison remain pending.** The initial
log snapshot contains zero detail cycles/captures. The selected Lebensmittel
Knupfer detail is fourth inside JOIN's recurring detail queue, but the domain
itself is rank 1,401 of 1,601 in the fleet's simple detail queue. Do not force
its due score or fetch a duplicate response. Copy native passive envelopes from
worker `/tmp/jobseek-join-go-detail-<slug>-<slot>.json` securely to local mode 0600.
Use `/tmp/jobseek-join-captured-parity.py <capture> /tmp/jobseek-join-detail-live`
with `PYTHONPATH=apps/crawler` for exact retained-byte Python/Go comparisons;
`--parse-detail` performs no HTTP. `/tmp/jobseek-join-detail-db-read.py` accepts
up to ten exact URLs for read-only title/description/hash/failure readback.
These pending checks are explicit; fixture parity is not live output proof.

Before any further crawler deploy or selector mutation, complete supported
`rollback c1`, then clear all **24** selectors under
`/run/lock/jobseek-crawler-mutation.lock` with
`/tmp/jobseek-post-smart-personio-selectors.py` against full revision
`cc1e273aa35b3f72b53eb82930fc4e39c9380e51` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. Restage only at the next promoted
revision after terminal deployment success, then reactivate c1. Helper SHA-256
remains `07f3dca2e8d0c0185b12817f76ab9e1e8e6775d2df128f9f123abdc803cb97a5`.
These instructions supersede the older revision/epoch checkpoints below.
Reverse JOIN details by setting `JOIN_GO_DETAIL_PERCENT=0` through the supported
cold procedure or by restoring the preceding release.

Continue implementation while the normal queue is pending. The fresh JSON-LD
primary-detail inventory contains **802 boards across 59 configs**, including
638 null/default configs, 79 rendered configs and 33 proxy users. Its protected
snapshot is `/tmp/jobseek-jsonld-detail-configs.json`. Port the shared parser and
HTTP transport, preserving each resolved custom/fallback/browser obligation.
Full enabled-profile coverage, Python worker/writer/browser retirement,
actual-workload whole-lane CPU/RAM/density/cost proof and final cutover remain
outstanding. The full migration goal remains active.

## Implementation checkpoint: JOIN Next.js details v0.13.884

The new native JOIN detail route defaults to 100% in Compose and the runtime.
A fresh read-only registry snapshot admits all **268 enabled JOIN Next.js detail
configurations**: 265 mapped boards across four forms and three with no fields,
which retain empty extraction without HTTP. Eight JSON-LD boards, resolved
fallback steps and provided B0 runtimes retain their existing obligations.
Deployment is recorded above; natural output readback and retained-byte
comparison remain pending.

The Go binary owns one direct detail fetch with redirects/cookies and adds no
retry. Non-200, missing data and malformed payloads preserve empty extraction
without a tombstone. Configured scalar/list conversions, OR truthiness,
trailing-comma cleanup, HTML attributes and full detail HTML parsing match 40
frozen cases from the actual Python parser. Fourteen frozen publisher-policy
cases cover the 65,536-character metadata bound, literal precedence, duplicate
attributes, source and companion policy. Installed-image CI exercises both
monitor and detail parsing offline. Native race/vet, focused tests and type
checks passed locally and the required hosted gates passed before merge.

Native passive capture now uses the existing `JOIN_CAPTURE_SLUGS` selector,
retaining pages 1/2 and at most four detail jobs per selected slug as mode 0600
exact-byte envelopes. No new overlay selector is needed. The unchanged 24-selector
helper and latest deployed revision below remain authoritative for supported
rollback/clear/stage/reactivation. Set `JOIN_GO_DETAIL_PERCENT=0` through the
supported cold procedure or restore the preceding release to reverse this route.

The private read-only pre-deploy baseline is retained mode 0600 at
`/tmp/jobseek-join-detail-predeploy.json`, SHA-256
`7872c4d494c32d5d04ea0ca504755315053354eb9e83c8f4a20abe3165a2cc97`.
It contains 4,072 active postings on the 268 admitted boards. Existing scrape
failure counts are 3,346 at zero, 175 at one, six at two, 544 at three and one
at four; these are a baseline, not failures introduced by this release.
Natural same-byte comparisons and persistence/failure readbacks remain pending.
Whole-lane resource/cost proof and Python/browser retirement remain outstanding.

## Production checkpoint: 2026-09-28 — JOIN monitors v0.13.883

PR #10118 merged as `9436ff0d9267c8e3657f1977ab35ced6195176c0`.
[Deployment 36360980005](https://github.com/colophon-group/jobseek/actions/runs/36360980005)
succeeded. All **276 enabled JOIN monitors** now select Go. The live census is
**4,725 Go / 3,154 Python** of 7,879 enabled monitors, with zero route errors.
The JOIN percentage is a release default of 100 in both Compose and the runtime;
no board was disabled. Required CI, installed-image parity, the native parser's
offline image fixtures and the crawler deploy gate passed.

Go configuration sync completed 6,013 companies and 7,879 boards at 00:17:50 UTC.
The refreshed pre-deploy baseline contains 276 boards and 4,169 active postings,
with zero consecutive failures. The first two natural Go cycles have exact
active DB URL readbacks and unchanged posting-ID/description-hash digests:
Cove Partners returned four jobs and Simply Payments a verified empty inventory.
Three further paginated runs have exact active DB URL and unchanged content
digest readbacks: Visus One five jobs from two requests, Trigon 15 from four,
and Pflegehelden six from two. Sixteen natural cycles were retained in the
later log snapshot, with five exact database readbacks covering 30 active rows.
The empty board had no recent discovery history and was excluded by the former
positive-history route rule. No failure count increased across the initial
276-board sweep. No due score or duplicate publisher request was forced.

Supported cold rollback retired c1 epoch 98 at 99 and restored all five schedules
with zero terminal drops/write fences. All **24 selectors** cleared under the
host lock, then restaged at the promoted full revision. C1 is active at **epoch
100**, accepted/audit_ok with five ready and zero inflight/dead records. The five
due times match the pre-rollback readback; all configured service health checks
pass and c2 remains dark. The next naturally due c1 task then reported success
at 00:26:40 UTC and committed at 00:26:42; its ready time advanced to
2026-09-29 00:26:42 UTC. The post-cycle audit still has all five records ready
and zero inflight work. This is task/schedule evidence, not paired resource
proof. The initial Typesense snapshot reports 388 successful
exports, zero errors, zero lag and healthy status. See
[sanitized production evidence](evidence/go-join-production-2026-09-28.json).

Before another crawler deploy or selector mutation, complete supported
`rollback c1`, then clear all **24** selectors under
`/run/lock/jobseek-crawler-mutation.lock` with
`/tmp/jobseek-post-smart-personio-selectors.py` against full revision
`9436ff0d9267c8e3657f1977ab35ced6195176c0` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. Restage only at the next promoted
revision, then reactivate c1. The helper SHA-256 remains
`07f3dca2e8d0c0185b12817f76ab9e1e8e6775d2df128f9f123abdc803cb97a5`.
These instructions supersede the older revision/epoch checkpoints below.
To reverse JOIN routing, set its percentage to zero and remove any exact JOIN
board selectors through the supported cold mutation procedure, or restore the
previous release.

The remaining JOIN monitors, including the largest inventories, need natural
output observation. The next implementation slice covers the **265 configured Next.js
detail boards across four exact configuration forms**. Three Next.js boards
have no configured fields and eight use JSON-LD; retain their resolved behaviors
and fallback/browser obligations. `JOIN_CAPTURE_SLUGS` captures Python monitor
responses and is inactive after the Go monitor route; add native passive capture
for retained-response comparisons. Complete the native TDM metadata precedence,
source and companion-policy parity with the shared Python checker while porting
the detail transport. The broader default-worker detail census resolves 3,040
boards, including six native detail selectors and 3,034 Python selections; B0
provided runtimes are tracked separately from that census.

This checkpoint covers monitor fetch/parser ownership and observed persistence.
Python worker/writer/detail and browser retirement, same-actual-workload
whole-lane CPU/RAM/density/cost proof and final cutover remain outstanding. The
full migration goal stays active.

## Implementation checkpoint: JOIN monitor broad route v0.13.883

The Go JOIN route now defaults to 100% in both the runtime and Compose. A
read-only production registry snapshot admits all **276 enabled JOIN monitors**,
all previously routed to Python. The pre-deploy baseline contains **4,169 active
postings** and zero consecutive failures; the largest recent inventory has 288
jobs. The private baseline is retained mode 0600 at
`/tmp/jobseek-join-baseline.json`, SHA-256
`34a10c37aa0446d5b281933deac10d1d64540d53625275cefd3540f10fe2abc2`.
This projects 4,725 Go / 3,154 Python monitors across the unchanged 7,879 enabled
boards. Deployment and the initial natural readbacks are now recorded above.
The refreshed private pre-deploy baseline is retained mode 0600 at
`/tmp/jobseek-join-predeploy.json`, SHA-256
`dcdfea6fd389fa03253bb701a3079977f4fd656f2bb8e069e2f14adf02808a9b`.

The native fetcher now preserves redirects, cookies, Python request headers,
three-attempt page retries and the four-million-character HTML prefix. Response
memory is bounded per page instead of rejecting a complete inventory once its
aggregate response bodies exceed 64 MiB. Required-page failure publishes no
inventory. The bridge bounds stdout while reading and reaps cancelled children.
All 52 frozen Python parser cases match, native race/vet checks pass, and 215
focused monitor/runtime/capture/nextdata tests pass. Installed-image CI repeats
the parser oracle with networking disabled. See the
[native contract](../apps/crawler/go/join-monitor/README.md).

JOIN detail scrapers and the Python worker/writer remain migration obligations;
this release changes monitor fetch/parser ownership. Use the latest deployed
checkpoint's supported rollback/24-selector clear before deployment, then stage
the same selectors at the newly promoted full revision and reactivate c1.
`JOIN_GO_PERCENT=0` reverses the broad route through that cold mutation procedure.
No additional publisher request or due score was forced for these checks.

## Production checkpoint: 2026-09-28 — SmartRecruiters and Personio v0.13.882

PR #10116 merged as `5eb32570ddb4b6561f3a2e14347b0c1c32315367`.
[Deployment 36357998180](https://github.com/colophon-group/jobseek/actions/runs/36357998180)
succeeded. All **129 enabled SmartRecruiters and 55 enabled Personio monitors**
now select Go, moving 178 more monitors from Python. The live census is
**4,449 Go / 3,430 Python** of 7,879 enabled monitors, with zero route errors.
Both provider percentages are release defaults of 100 in Compose and the
runtime. The old Personio 5% overlay pin was removed. No board was disabled.

Required CI, installed-image parity and the crawler deploy gate passed. Go
configuration sync completed 6,013 companies and 7,879 boards at 23:23:53 UTC.
The refreshed pre-deploy baseline contains 184 boards and 113,171 active postings,
including Domino's 24,825 rows. Nine natural Go cycles have exact active DB URL
readbacks and unchanged posting-ID/description-hash digests: AUTO1 588, ASML 17,
Endava 173, Louis Dreyfus 430 and Syngenta 543 SmartRecruiters jobs; Cylib 9,
Eraneos 21, NVision 12 and Ohpen 14 Personio jobs. AUTO1 and Syngenta are custom
career domains and exceed the former 500-posting route ceiling. No failure
count increased across the final 184-board readback; ARX was already quarantined
with 19 failures. No due score or duplicate publisher request was forced.

Supported cold rollback retired c1 epoch 96 at 97 and restored all five
schedules with zero terminal drops/write fences. The previous **25 selectors**
cleared under the host lock. The reduced **24-selector** overlay was staged
against the promoted full revision and c1 reactivated at **epoch 98**. Its
queue audit is accepted/audit_ok, with five ready and zero inflight/dead records.
All configured service health checks pass; c2 remains dark. An initial Typesense
snapshot reports 539 successful exports, zero errors, zero lag and healthy
status. See [sanitized production evidence](evidence/go-smart-personio-production-2026-09-28.json).

Before another crawler deploy or selector mutation, complete supported
`rollback c1`, then clear all **24** selectors under
`/run/lock/jobseek-crawler-mutation.lock` with
`/tmp/jobseek-post-smart-personio-selectors.py` against full revision
`5eb32570ddb4b6561f3a2e14347b0c1c32315367` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. Restage only at the next promoted
revision, then reactivate c1. The helper SHA-256 is
`07f3dca2e8d0c0185b12817f76ab9e1e8e6775d2df128f9f123abdc803cb97a5`.
These instructions supersede the historical 25-selector checkpoints below.
To reverse the two broad routes, set their percentages to zero and remove their
exact monitor board selectors through the supported cold mutation procedure,
or restore the previous release.

This checkpoint covers monitor fetch/parser ownership and observed persistence.
The largest boards and the newly admitted Personio custom-URL configurations
still need natural output observation. Remaining Python/detail/browser work,
same-actual-workload whole-lane CPU/RAM/density/cost proof and final retirement
remain open; the full migration goal stays active.

## Production checkpoint: 2026-09-28 — Recruitee and Pinpoint routing v0.13.881

PR #10114 merged as `ae518d09610b3570b706c67bb7862ee18cb7f267`.
[Deployment 36355148463](https://github.com/colophon-group/jobseek/actions/runs/36355148463)
succeeded. All **115 enabled Recruitee and 104 enabled Pinpoint monitors** now
select Go, including 29 configurations excluded by the old direct-host rule.
The live census is **4,271 Go / 3,608 Python** of 7,879 enabled monitors, with
zero route errors. The pre-deploy baseline covers 219 boards and 4,791 active
postings. No board was disabled.

Required CI and installed-image parity passed. The release's Go configuration
sync completed 6,013 companies and 7,879 boards at 22:35:17 UTC. Retained
actual responses matched all nine rich fields for 58 jobs without origin
traffic. The first four natural Go cycles include both providers: Teya returned
three jobs, Adarga four, Smarsh four, and Spark a verified empty inventory.
A newly admitted custom-domain board, Dronamics, returned 16 jobs from a
300,938-byte response at 22:41:49 UTC. Tether returned 180 and Rowden
Technologies 35. All seven readbacks match their active DB canonical URL sets
and their pre-deploy posting-ID/description-hash digests, including the
custom-domain board. Ten natural Go cycles completed in the first observation.
No selected board's failure count increased in the final 219-board readback.
No due scores or publisher requests were forced.

Supported rollback retired c1 epoch 94 at 95, restored all five schedules and
left zero terminal drops/write fences. All **25 selectors** were cleared under
lock and restaged at the promoted revision. C1 is active at **epoch 96**,
accepted/audit_ok with five ready and zero inflight/dead records. All configured
service health checks pass; c2 remains dark. Typesense reported 109 successful
exports, zero errors, zero lag and healthy status in the initial snapshot. See
[sanitized production evidence](evidence/go-recruitee-pinpoint-production-2026-09-28.json).

Before another crawler deployment or selector mutation, complete supported
`rollback c1`, then clear the same 25 selectors under
`/run/lock/jobseek-crawler-mutation.lock` using
`/tmp/jobseek-post-go-experience-selectors.py` against full revision
`ae518d09610b3570b706c67bb7862ee18cb7f267` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. Restage only at the next promoted
revision, then reactivate c1. Recruitee/Pinpoint percentages are release
defaults of 100, not extra host selectors. These instructions supersede the
historical checkpoints below.

A later readback of the preceding SuccessFactors release found 26 natural Go
cycles and two already-failing boards with one additional failure each:
Kaufland's connection reset after 41 streamed batches and Vibrant M's redirect
limit. Their active URL and posting-content hashes remained unchanged (8,704
and 878 rows respectively); neither failure caused deletions. These are
follow-up observations, not a claim of all-board success.

Full Python/Chromium retirement and same-actual-workload whole-lane
CPU/RAM/density/cost proof remain open.

## Implementation checkpoint: SmartRecruiters and Personio broad routes v0.13.882

The existing Go SmartRecruiters and Personio monitor binaries and Compose
environment now have default 100% routes for their supported configurations.
The previous three-run,
500-posting eligibility ceiling no longer excludes large boards or new boards;
the native 50,000-posting boundary and bounded response/output accounting remain.
Personio follows the Python monitor's configured slug precedence, preferred
`.de`/`.com` domain and custom-career-URL behavior. Its Python bridge now reads
child output with a bound and reaps a cancelled child. Explicit board selectors
and percentage-zero rollback remain available. The previous production
Compose default pins SmartRecruiters at 0%, while its selector overlay pins
Personio at 5%; the next supported rollback/clear must remove the Personio
pin, and the post-deploy stage helper must preserve the other 24 exact
selectors without reintroducing it.

A read-only 2026-09-28 production registry snapshot found **129 enabled
SmartRecruiters and 55 enabled Personio** boards. All 184 select Go with this
code. Their pre-deploy active database baseline contains 113,170 postings:
112,560 SmartRecruiters and 610 Personio. SmartRecruiters includes Domino's
24,825 active rows, Accor 6,263 and AECOM 5,309; none of its 129 boards has a
consecutive failure. Personio has one previously quarantined board with a
failure. The complete private baseline JSON is retained mode 0600 outside the
repository at `/tmp/jobseek-smartrecruiters-personio-baseline.json` with SHA-256
`7a1ea55f6c8f2a72ab70671d62525952eb12f2ced3c73a19468a8161fdd049d3`.
Twelve focused runtime tests and both native modules' tests pass locally, with
the Personio race detector and the SmartRecruiters race detector in its runtime
test. The successful production deploy and nine natural Go output/database
readbacks are recorded in the latest checkpoint above. The refreshed pre-deploy
baseline is retained mode 0600 at `/tmp/jobseek-smartrecruiters-personio-predeploy.json`,
SHA-256 `8f1b3a3e90702b3729aed755ca60fff6acdec9d7d9b36fb456e71e793f5b6766`.
No publisher request was added for this verification. Native per-mode posting
limits remain 50,000 for ordinary/localized discovery and 500 for canonical
job-location discovery.

## Implementation checkpoint: Recruitee and Pinpoint routing v0.13.881

The native runtimes now cover all **115 Recruitee and 104 Pinpoint** configurations
in the enabled registry, including 29 configurations previously excluded by
hosted-domain restrictions. Go preserves Python's explicit API-base/slug/URL
precedence and supports public HTTPS redirects, with per-request accounting,
TDM checks and a 30-second read-inactivity timeout. Native payload bounds rise
from 16 to 64 MiB; child stdout retention and terminate/kill cleanup are bounded.
Shared URL/job filters, URL identity transformations and writer policies remain
in place. Both provider percentages default to 100 without pilot history/count
requirements. Explicit board selectors still take precedence for reversal.

Verification maps all 219 current configurations to frozen Python request
endpoints and checks 231 frozen Python parser cases, including tolerant fields,
normalization, rich metadata and errors. Six retained actual API responses
match all nine rich fields for 58 jobs. Go race/vet and 445 focused provider,
runtime, processing and policy tests passed (the two new allowlist test cases
were corrected to use the shared full-match contract and then passed). CI also
runs the parser cases through the installed binaries with networking disabled.
The read-only baseline covers 219 enabled boards and 4,791 active postings;
all have zero consecutive failures, including 13 already marked gone and 25
suspect. No board is disabled and no extra origin request is issued.

Deployment and natural-run readback are recorded above. The v0.13.880
checkpoint below was the supported rollback source for this release. These new percentages are release
defaults, not additional host selectors. Supported percentage reversal sets
both `RECRUITEE_GO_PERCENT` and `PINPOINT_GO_PERCENT` to zero and removes their
explicit board-ID selectors, or restores the prior release.

This is monitor fetch/parser ownership. Python worker/writer/detail/browser
retirement and actual-workload whole-lane resource evidence remain outstanding.

## Production checkpoint: 2026-09-27 — SuccessFactors RSS routing v0.13.880

PR #10111 merged as `aa9b5e4f046c8964bb557220cbd23ec750909bc5`.
[Deployment 36352086220](https://github.com/colophon-group/jobseek/actions/runs/36352086220)
succeeded. **216 of 227 enabled SuccessFactors RSS monitors now select Go**, up
from one. The live census is **4,058 Go / 3,821 Python** of 7,879 enabled monitors,
with zero route errors. The remaining eleven SuccessFactors profiles use legacy
HTML/XML, RMK, or inline enrichment requests. No boards were disabled.

Required CI and installed-image parity passed. Go configuration sync completed
6,013 companies and 7,879 boards at 21:44:00 UTC. The read-only baseline covers
216 boards and 236,664 active postings; it includes ten already quarantined and
ten suspect boards. No consecutive-failure count increased in the first
post-activation readback.

Newly admitted Honda Asia & Oceania returned 20 jobs at 21:47:34 UTC and Ferrara
returned 88 at 21:47:54 UTC. Both natural Go canonical URL hashes match their
active database rows exactly. Posting IDs and description hashes also match
the pre-deploy baseline; both boards have zero consecutive failures. ZF then
completed a natural 814-job run at 21:50:48 UTC from a 4,016,882-byte feed.
All 814 URLs match active DB rows, with unchanged posting IDs/description hashes
and zero failures. This exceeds the old 500-job pilot ceiling. Five natural
Go cycles have completed. No due times or publisher requests were forced.
The greater than 256 MiB fixture and retained Mobiliar replay remain bounded
parser evidence, not whole-lane resource measurements.

Supported rollback retired epoch 92 at 93, restored all five schedules, and
left zero terminal drops or write fences. The unchanged **25 selectors** were
cleared under lock and restaged at the promoted revision. C1 is active at
**epoch 94**, accepted/audit_ok, with five ready records and zero inflight/dead.
All configured service health checks pass; C2 remains dark. The exporter
snapshot reports 220 successful exports, zero errors, zero lag and healthy
Typesense status. See the sanitized
[production evidence](evidence/go-successfactors-production-2026-09-27.json).

Before another crawler deployment or selector mutation, complete supported
`rollback c1`, then clear all 25 selectors with
`/tmp/jobseek-post-go-experience-selectors.py` under
`/run/lock/jobseek-crawler-mutation.lock`, against full revision
`aa9b5e4f046c8964bb557220cbd23ec750909bc5` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. Restage only at the next promoted
revision, then reactivate c1. SuccessFactors' percentage is now a release
default of 100, not an additional host selector. These instructions supersede
the historical release/epoch checkpoints below.

The full transition remains active: Python still owns 3,821 monitors, worker
and writer stages, remaining CPU/detail/browser work. Whole-lane actual-workload
CPU/RAM/density/cost proof and final retirement/cutover remain outstanding.

## Implementation checkpoint: SuccessFactors RSS feed routing v0.13.880

Go now defaults to all supported direct SuccessFactors feeds, including explicit
`variant=feed`, configured cross-domain Google feeds, the category RSS endpoint,
and shared URL filters/transforms and writer identity-migration metadata. The
pilot-only recent-count requirement is removed. A read-only replay of all 227
current configurations selects **216 Go monitors**, up from one. Eleven remain
Python: three legacy HTML, one legacy XML, three RMK, and four profiles with
inline detail-field or job-invite identity requests. None are disabled.

Native parsing now matches Python's Unicode case folding, whitespace, exact
XML namespaces, first repeated field, and text before nested child elements.
Entity-decoded description whitespace is preserved. The aggregate 256 MiB
pilot feed cap is replaced by a 32 MiB XML token/item read window, preserving
streaming for large boards. HTTP reads use the Python path's 30-second
inactivity budget instead of charging downstream DB backpressure against a
five-minute whole-request limit. The 216-board baseline has 236,664 active postings.
Thirty-two frozen Python
parser cases pass in Go; CI also replays them through the installed binary with
networking disabled. The retained 482,801-byte Mobiliar feed matches all 71 rich
jobs exactly, including all descriptions, locations and metadata. Its source
SHA-256 is `0a65c6e9d7fbe329d95fa59e126d0cbe72770033897a27a6ed2ba79d7c228ff4`.

Verification passed 228 focused runtime/RSS/shared monitor tests, Go race/vet,
Ruff/Pyright and the offline native verifier. A streamed synthetic feed above
256 MiB passes without retaining the inventory; read-inactivity and partial-feed
retry boundaries are covered. Streamed output, HTTP/TDM failure
classification and downstream writer/drop policy remain in place. The bridge
now reaps cancelled/stuck children after bounded termination and records a
postprocessed canonical URL hash for DB comparison. No extra publisher requests
were made. Deployment and natural output readback are recorded above. This slice incorporates
PR #10100 (v0.13.879), including its authoritative TDM reservation check before
Go dispatch. The TDM deployment and documentation checkpoint are complete:
promoted revision `265d7e8b8f12beabff3d5e7e131f2e8eb168903d`, c1 epoch 92,
with the unchanged 25 selectors. That checkpoint was the rollback source for
this release; current operating instructions are in the production section above.

For reversal, restore the previous release or use the supported cold
configuration procedure with `SUCCESSFACTORS_RSS_GO_PERCENT=0`; explicit
`SUCCESSFACTORS_RSS_GO_BOARD_IDS` still take precedence and must also be cleared
for full provider reversal. This slice does not establish complete provider or
Python/browser retirement, or whole-lane resource efficiency.

## Production checkpoint: 2026-09-27 — TDM follow-up v0.13.879

PR #10100 merged as `265d7e8b8f12beabff3d5e7e131f2e8eb168903d`.
[Crawler deployment 36349867411](https://github.com/colophon-group/jobseek/actions/runs/36349867411)
and [web deployment 36349867352](https://github.com/colophon-group/jobseek/actions/runs/36349867352)
both succeeded. Observed TDM reservations now persist through crawler storage,
Typesense export and later mining consumers. Ordinary listings and description
display remain available. Detection gaps and the complete synthetic production
rehearsal remain under #10090; this release does not close that issue.

Crawler migration 0034 and the optional indexed boolean `tdm_reserved` were
installed before the web rollout. An initial database lock timeout applied no
changes; the migration then completed during a bounded writer pause. The
Typesense PATCH exceeded the client timeout but completed server-side, verified
by schema read-back and a live query showing an existing unreserved document
still eligible. The ambiguous PATCH was not blindly replayed. All existing
writers restarted healthy before merge.

The refreshed PR passed Required CI, installed-image parity and its exact-head
deployment gate: 13,507 crawler tests passed, with 44 skipped, plus the web,
Go and database checks. Another 476 focused processing/runtime tests passed
locally after incorporating current main.

Supported c1 rollback retired epoch 90 at **91**, restored all five schedules,
and left zero terminal drops or write fences. All **25 selectors** were cleared
under lock and restaged at the promoted revision. C1 is active at **epoch 92**,
accepted/audit_ok: five ready records, zero inflight or dead. All long-lived
services are running, and all configured health checks pass. The initial
post-activation exporter snapshot recorded 136 successes, zero errors, zero
lag and healthy Typesense status. See the sanitized
[release evidence](evidence/tdm-production-2026-09-27.json).

Before another crawler deployment or selector mutation, finish supported
`rollback c1`, then clear the unchanged 25 selectors using
`/tmp/jobseek-post-go-experience-selectors.py` under
`/run/lock/jobseek-crawler-mutation.lock`, against full revision
`265d7e8b8f12beabff3d5e7e131f2e8eb168903d` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. Restage only at the next successfully
promoted revision, then reactivate c1. These instructions supersede the earlier
revision/epoch checkpoints below. No extraction ownership or retirement claim
is added by this release.

## Production checkpoint: 2026-09-27 — complete Teamtailor RSS routing v0.13.878

PR #10109 merged as `ca3d0c244b6e8288227276c0151f80edc1c3d988`.
[Deployment 36347539962](https://github.com/colophon-group/jobseek/actions/runs/36347539962)
succeeded. **All 162 enabled Teamtailor RSS monitors now select Go**, up from
one. The live census is **3,843 Go / 4,036 Python** of 7,879 enabled monitors,
with zero route errors. No enabled boards were removed; the baseline includes
four already quarantined boards and five new boards without history.

The retained real Sellpy feed matches Python across all seven rich fields for
11 jobs. Verification passed 217 focused runtime/RSS/shared monitor tests, Go
race/vet, Ruff/Pyright, Required CI and installed-image parity. Go configuration
sync completed 6,013 companies and 7,879 boards at 20:27:50 UTC.

Two newly admitted boards completed natural Go runs. Huawei Finland R&D
returned 15 jobs at 20:28:15 UTC, matching the active DB URL hash exactly;
posting IDs and description hashes also match the pre-deploy baseline.
Doconomy returned one job after c1 reactivation at 20:31:41 UTC, also with an
exact DB URL hash. Both have zero consecutive failures. The read-only baseline
covers 162 boards and 3,178 active postings. No due scores or publisher requests
were forced. The preceding Ashby rollout also has new custom-domain evidence:
Smallpdf's three jobs match its natural Go output and active DB exactly.

Supported rollback retired epoch 88 at 89, restored all five schedules and
left zero terminal drops/write fences. The unchanged **25 selectors** were
cleared under lock and restaged at the promoted revision. C1 is active at
**epoch 90**, accepted/audit_ok, with five ready records and zero inflight/dead;
all long-lived services are healthy. C2 remains dark. The post-activation
snapshot reports 121 successful Typesense exports, zero errors, zero lag and
healthy status. See the sanitized
[production evidence](evidence/go-teamtailor-production-2026-09-27.json).

Before another deployment or selector mutation, complete supported `rollback c1`,
then clear all **25** selectors using `/tmp/jobseek-post-go-experience-selectors.py`
under `/run/lock/jobseek-crawler-mutation.lock`, against full revision
`ca3d0c244b6e8288227276c0151f80edc1c3d988` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. Restage only at the next promoted
revision, then reactivate c1. Teamtailor's percentage is now a release default
of 100; it is not an additional host selector. These instructions supersede
older release/epoch instructions below.

Remaining work includes 4,036 Python monitors, detail/browser profiles, Python
worker/writer and CPU stages, plus same-workload whole-lane efficiency and final
cutover/reversal. No full retirement or new whole-lane resource claim is made.
A read-only next-slice census found 227 enabled SuccessFactors RSS boards:
188 pass current Go feed eligibility and 39 need configuration/profile work.

## Implementation checkpoint: complete Teamtailor RSS routing v0.13.878

The Teamtailor RSS runtime now defaults to Go for every supported direct feed,
without the pilot's requirement for three recent nonzero counts below 501.
A read-only production census found 162 enabled Teamtailor RSS boards, all with
supported configurations; five have no history or active rows. The pre-release
baseline covers 3,178 active postings. This change moves the 161 remaining
Python Teamtailor RSS monitors to the existing native fetch/parser path.

Transport/configuration validation, 100-item pagination, 50,000-job truncation,
TDM handling and shared downstream filtering/URL identity/writer policies are
preserved. The bridge bounds stdout while reading and reaps a stuck child after
a bounded terminate/kill sequence, including cancellation. A new
`go_teamtailor_rss.monitor_postprocessed` event records the canonical URL set
hash after filtering and transformations, for exact database comparisons.

Replaying the retained 69,210-byte Sellpy feed through current Python and Go
matched all seven rich fields for 11 jobs (canonical field SHA-256
`3e156cb0a84a47f5c2d64307d6626e079694e033b9aa871c0c56bf6028accbb5`).
No publisher traffic was generated. Deployment and natural-run evidence are
recorded in the production checkpoint above.

For percentage rollback set `TEAMTAILOR_RSS_GO_PERCENT=0` through the supported
cold configuration procedure. Explicit `TEAMTAILOR_RSS_GO_BOARD_IDS` still take
precedence, so remove those too for full provider reversal, or restore the
previous release for the existing Sellpy-only route. This is monitor ownership;
Python worker/writer stages and the full retirement/resource gates remain open.

## Production checkpoint: 2026-09-27 — complete Ashby monitor routing v0.13.877

PR #10107 merged as `0893bd935b629601714dde3b828bcd3c8088dfe9`.
[Deployment 36344767917](https://github.com/colophon-group/jobseek/actions/runs/36344767917)
succeeded. **All 935 currently enabled Ashby monitors now select Go**, including
the 57 configurations excluded by the previous route. The live census is
**3,682 Go / 4,197 Python** of 7,879 enabled monitors, with zero routing errors.
This is monitor-stage coverage; Python worker processing and Nord Security's
configured JSON-LD detail path remain separate migration obligations.

The request mapping preserves Python precedence and encoding for explicit or
direct-URL-derived tokens, spaces/dots, custom career domains and matching
legacy metadata. Writer-owned drop settings and separate detail configuration
are unchanged. Verification passed 136 focused Ashby/routing tests, 57 existing
writer/enrichment/drop tests, Go race/vet, Ruff/Pyright, Required CI and installed
image parity. All 57 frozen Python/httpx request endpoints also pass through the
installed native binary with networking disabled.

The actual release's Go configuration sync completed 6,013 CSV companies and
7,879 boards at 19:44:35 UTC. Supported rollback retired epoch 86 at 87 and
restored all five schedules with zero terminal drops/write fences. The same
**25 selectors** were cleared under lock and staged at the promoted revision.
C1 is active at **epoch 88**, accepted/audit_ok, with five ready records and
zero inflight/dead. All long-lived services are healthy; C2 remains dark.
Typesense reported 64 successful exports, zero errors, zero lag and healthy
status in the initial post-activation snapshot.

Six natural Go monitor cycles were observed after activation. Abridge, newly
admitted through direct-URL token derivation, returned 47 URLs with an exact
active database URL hash and zero failures. Its posting-ID/description-hash
digest also matches the pre-deploy baseline. Other new configuration forms
continue through their normal schedules. The read-only baseline covers all
57 newly admitted boards and 2,579 active postings. See the sanitized
[production evidence](evidence/go-ashby-production-2026-09-27.json) for baseline
hashes, exact selector values, route census and activation evidence. No origin
requests or due times were forced.

Before another deployment or selector mutation, complete supported `rollback c1`,
then clear the current **25** selectors with
`/tmp/jobseek-post-go-experience-selectors.py` under
`/run/lock/jobseek-crawler-mutation.lock`, against full revision
`0893bd935b629601714dde3b828bcd3c8088dfe9` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. Restage only at the next promoted
revision, then reactivate c1. These current instructions supersede the older
release/epoch instructions below. The full documented #7966 requirements remain
unmet while Python and Chromium own production work; this slice makes no new
whole-lane CPU/RAM/density/cost claim.

## Production checkpoint: 2026-09-27 — Go experience and Ashby expansion v0.13.876

PR #10104 merged as `7da64809897c5e9d3ca13d2537318c707effc9a0`.
[Deployment 36342254765](https://github.com/colophon-group/jobseek/actions/runs/36342254765)
succeeded. **Go now also owns experience extraction in shared monitor/detail
CPU processing.** The installed-image oracle passed all 1,967 cases; 43 focused
tests and the same-content replay of 256 stored postings passed. Local replay
used approximately 21% less experience CPU including IPC and the native child;
this remains stage evidence, not whole-lane RAM/density/cost proof.

The first natural readback matched all six populated predictions among 52
postings. After c1 reactivation, all 11 populated predictions among 48 postings
matched persisted minimum/maximum experience. A fresh-worker snapshot recorded
2,744 successful native experience calls and zero enrichment execution errors.
Typesense reported 101 successful exports, zero errors, zero lag and healthy
status. The actual release's Go configuration sync completed 7,879 boards and
6,013 CSV companies at 19:03:56 UTC.

**Ashby now selects 100% of strictly supported configurations:** 878 Go routes,
up from 209. Its obsolete Python-only passive capture selector was removed.
The actual enabled-monitor census is **3,625 Go / 4,254 Python** of 7,879,
with zero routing errors. Fifty-seven other Ashby configurations remain Python;
100% is the supported-profile percentage, not a claim of full provider coverage.
Two newly admitted boards completed natural Go runs: Orb returned 25 URLs,
matching its active database exactly; Spekit returned four URLs, all matching
freshly seen database rows. Spekit's unchanged drop guard retained six older
active rows last seen between July 9 and September 11. Its logged suspect-drop
history predates this rollout; both boards have zero consecutive failures.

Supported rollback retired epoch 84 at 85, restored all five schedules and
left zero drops/write fences. After deployment, the reviewed **25-selector**
set was staged against the exact promoted revision. C1 is active at **epoch 86**,
accepted/audit_ok, with five ready records and zero inflight/dead; all services
are healthy. C2 remains dark. The sanitized
[production evidence](evidence/go-experience-production-2026-09-27.json) records
selectors, readbacks, natural output hashes and the remaining Python monitor census.

Before another deployment or selector mutation, complete supported `rollback c1`,
then clear the current **25** selectors with
`/tmp/jobseek-post-go-experience-selectors.py` under
`/run/lock/jobseek-crawler-mutation.lock`, against full revision
`7da64809897c5e9d3ca13d2537318c707effc9a0` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. The old 26-selector helper no longer
matches production. Restage only against the next promoted revision, then
reactivate c1. No due times or publisher traffic were forced.

The owner closed #7966 on September 27, but its documented full-retirement
requirements remain unmet: Python workers, remaining extraction/enrichment and
browser profiles, whole-lane efficiency and final cutover/reversal still need
implementation and evidence. This checkpoint supersedes the operational
selector/epoch instructions in the historical checkpoints below.

## Production checkpoint: 2026-09-27 — shared Go classification v0.13.875

PR #10102 merged as `7f27c5006e45b484801494c149b262371bbf307c`.
[Deployment 36340121669](https://github.com/colophon-group/jobseek/actions/runs/36340121669)
succeeded. **Go now owns occupation, seniority and technology matching in
monitor and detail CPU processing.** Each of the three HTTP workers and the
browser worker has one resident native matcher; observed retained RSS is
about 11–12 MiB per child. The Python worker, remaining CPU stages (HTML,
language, location, salary, experience), scheduling and persistence remain
in scope for #7966.

Required CI, installed-runtime contracts and the installed-image offline Go
parity step passed. Local verification covered 336 focused tests, 1,568 frozen
title cases, 2,056 technology inputs and exact same-byte replay of 256 actual
stored postings from 128 boards and eight locales. Local counterbalanced
replay used approximately 44% less classification CPU including native child
and bridge; this does not establish whole-lane RAM or cost savings.
See [replay evidence](evidence/go-job-enrichment-replay-2026-09-27.json).

Natural production execution recorded 3,560 successful Go classifications
before c1 reactivation and 17,475 in the later fresh-worker snapshot, with
zero enrichment errors. A readback of 43 new postings matched stored values
(mostly null discovery stubs). Among 25 recent scrapes, all five populated
occupation predictions, two seniority predictions and seven technology sets
matched persisted fields. Each comparison uses current Python reference
semantics against stored inputs; null-retention and structured-internship
policy remain intact. Typesense reported 268 successful exports, zero errors,
zero lag and healthy status. The actual release's Go sync completed 7,879
boards and 6,013 CSV companies at 18:29:08 UTC.

Supported c1 rollback retired epoch 82 at 83, restored all five schedules,
and left zero drops/write fences. All 26 exact selectors were cleared under
the mutation lock, then restaged against the promoted revision above.
C1 is active at **epoch 84**, accepted/audit_ok, with five ready records and
zero inflight/dead; all services are healthy. C2 remains dark. The unchanged
monitor route census is 2,956 Go / 4,923 Python, with zero routing errors.
Natural newly selected SmartRecruiters/Pinpoint route proof remains separate
from this shared classification evidence.

The durable [production record](evidence/go-job-enrichment-production-2026-09-27.json)
contains sanitized readbacks, metrics, process memory and exact selectors.
Before another deployment or selector mutation, use supported `rollback c1`,
then clear the current **26** selectors with
`/tmp/jobseek-post-smartrecruiters-selectors.py` under
`/run/lock/jobseek-crawler-mutation.lock`, against full revision
`7f27c5006e45b484801494c149b262371bbf307c` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. Restage only against the next promoted
revision and reactivate c1. No due times or publisher traffic were forced.
The full #7966 migration and final whole-lane efficiency/retirement gate remain open.

## Production checkpoint: 2026-09-27 — SmartRecruiters v0.13.874

PR #10099 merged as `2be9bd9d2465043a271c44722f2394ba23a9b00e`.
[Deployment 36336660636](https://github.com/colophon-group/jobseek/actions/runs/36336660636)
succeeded. The new Go binary owns selected SmartRecruiters monitor and
scheduled detail extraction. All three configured localized identity modes
are implemented; the initial routes select ordinary Lonza, Gousto and
Dailymotion monitors plus Swiss Medical Network's `job-location-v1` monitor.
Detail routes select Lonza, Gousto, Dailymotion, Domino's and Northwestern
Medicine. The last two already have work in the normal detail queue; no due
times or origin traffic were forced. **Natural Go output and database/content
proof for these newly selected routes remain pending.**

Required CI and installed-image parity passed on the exact reviewed head.
The focused suite passed 123 tests; the native binary matches 60 offline
Python monitor cases and 26 detail cases. During the actual deployment, Go
configuration sync committed and published 7,879 boards, completed 6,013 CSV
companies at 17:33:05 UTC, and reported zero unresolved dead letters.

Supported c1 rollback retired epoch 80 at 81, restored all five schedules,
and left zero drops/write fences. The prior 24 selectors were cleared under
the host mutation lock. After promotion, the new **26-selector** set was
staged at the exact full revision above. Supported activation restored c1 at
**epoch 82**; its conservation audit is accepted/audit_ok with five ready
records, zero inflight/dead, and all services healthy. The exporter snapshot
reported 94 successful documents, zero errors, zero lag and healthy Typesense.
The live monitor route census is **2,956 Go / 4,923 Python** of 7,879 enabled
boards, with zero route-resolution errors. This census measures monitor
routing, not complete worker/runtime ownership.

Two normal Python Pinpoint captures also passed same-byte comparison across
all nine rich fields: Accelercomm (12 jobs, 73,926 bytes) and Penumbra
(three jobs, 26,305 bytes). Their active database URL hashes match exactly,
with zero failures. Their existing Go routes are now selected alongside
Bright Network; their first natural Go run is pending. In the existing
Workable 25% rollout, Bjak naturally returned 10 jobs in Go at 17:13:34 UTC,
with exact database URL parity and zero failures. Sanitized hashes, baseline
identities, selector values and evidence are in
[the release record](evidence/go-smartrecruiters-production-2026-09-27.json).
Raw passive captures remain local mode-0600 files.

Before the next crawler deployment or selector mutation, complete supported
`rollback c1`. Then clear all **26** exact selectors with
`/tmp/jobseek-post-smartrecruiters-selectors.py` under
`/run/lock/jobseek-crawler-mutation.lock`, against the actual deployed full
revision above and `https://kandou.bamboohr.com/careers/310`. The old 24-selector
helper no longer matches production. Stage only at the next promoted
revision, then reactivate c1. C2 remains dark. The full #7966 gate remains
open for other extraction/browser profiles, Python runtime ownership,
actual-workload whole-lane resource evidence, and final cutover/reversal.

## Implementation: SmartRecruiters monitor and detail, v0.13.874

The Go SmartRecruiters binary now implements ordinary publication discovery,
all three configured localized identity modes, and scheduled provider detail
extraction. Default-off monitor/detail selectors are independent. The unchanged
writer retains queue, database, confirmed-drop and enrichment policies. See
[the module contract](../apps/crawler/go/smartrecruiters-monitor/README.md).

Offline verification covers 60 Python monitor cases and 26 detail cases,
including complete rich fields, source identity, language variants, pagination
and retry boundaries. CI also compares those outputs through the installed
binary with network disabled. The runtime reads bounded output and terminates
and reaps the child on cancellation or overflow. Scheduled details retain a
single request and empty content on non-200; Go additionally applies the
monitor's 1 MiB bound and TDM header/meta protections.

The production checkpoint above records the subsequent release and activation.
These offline fixtures remain implementation evidence; they do not establish
natural production content or whole-lane resource parity. Continue natural
monitor/detail and database/content/failure readback without changing due times.

## Production checkpoint: 2026-09-27 — Go configuration sync v0.13.873

PR #10096 merged as `1f37e47ef036c1b08a5ca45dfca94cd9d7e3dbf6`.
[Deployment 36332710204](https://github.com/colophon-group/jobseek/actions/runs/36332710204)
succeeded. **Go now owns production configuration sync end to end:** CSV
preparation, taxonomy/company/description and board database transactions,
pending taxonomy resolution, Redis publication, dead-letter inspection and
Typesense publication. The Python CLI execs Go before opening runtime pools;
retained Python implementations are comparison/rollback references.

The actual release committed **7,879 boards** at 16:28:41 UTC, published
**7,879 schedules** and removed **133 retired-board queue entries** at
16:28:43 UTC. Dead-letter inspection found 24 records and zero unresolved.
Typesense taxonomy publication included 37,526 locations, 562 occupations,
36 seniorities and 186 technologies; the complete 6,013-company/7,879-board
sync finished at 16:29:23 UTC. Local mode-0600 deployment evidence is
`/tmp/jobseek-go-registry-deploy-36332710204.log`.

Read-only before/after identity digests match for all **6,039 database
companies and 8,013 database boards**, including retained historical rows.
All **7,879 enabled board configurations** in Redis match PostgreSQL URL, crawler
type, company ID, throttle domain and monitor/detail browser flags, with
zero mismatches. The sanitized record, including exact hashes and selector
values, is [the release evidence](evidence/go-registry-production-2026-09-27.json).

Required CI, installed-image parity and the exact-head deployment gate passed.
The first image check exposed a changed missing-mount diagnostic; the fix
preserves that contract and adds an installed Go dry-run with staged read-only
CSV data. Before promotion, supported c1 rollback retired epoch 78 at 79,
restored all five schedules, and left zero drops/write fences. Scheduled Go
reconciliation first completed 16 partitions and 356,179 local/remote rows
with zero differences; its live process was allowed to finish. All old 23
selectors were then cleared under the host mutation lock.

The new 24-selector set was staged against the exact promoted revision,
adding the existing strict `WORKABLE_GO_PERCENT=25` route after all four
explicit Workable boards completed natural Go runs with exact database URL
parity and zero failures. Its current union covers 15 boards, adding 11.
C1 is active at **epoch 80**, with accepted/audit_ok conservation: five ready
records, zero inflight or dead, and all services healthy. An exporter snapshot
reported 194 successful documents, zero errors and zero lag. The current
monitor route census is **2,950 Go / 4,929 Python** out of 7,879 enabled
boards, with zero route-resolution errors. Broader natural Workable runs are
pending; no due times were forced.
Use `/tmp/jobseek-post-go-registry-selectors.py` for subsequent cleanup;
the older 23-selector helper no longer matches the live environment. Before
any later crawler deployment or selector mutation, supported `rollback c1`
must finish first. Then use the new helper in `clear` mode under
`/run/lock/jobseek-crawler-mutation.lock`, against the current full deployed
revision above and `https://kandou.bamboohr.com/careers/310`. Stage only
after successful promotion at the new revision, then reactivate c1.

The full #7966 gate remains open: Python workers and remaining extraction,
enrichment, persistence and browser profiles still own production work;
whole-lane resource/cost comparison and final retirement are unfinished.

## Production checkpoint: 2026-09-27 — Go queue sync v0.13.871

PR #10093 merged as `ce5dfd821ca6e6b95d0b79a9eb534c26ddd486ec`.
[Crawler deployment 36328634446](https://github.com/colophon-group/jobseek/actions/runs/36328634446)
and [host launcher deployment 36328634440](https://github.com/colophon-group/jobseek/actions/runs/36328634440)
both succeeded. Production configuration sync now publishes committed board
queues in Go. Its actual deployment log records **7,879 schedules enqueued
and 133 retired-board queue entries removed**, followed by dead-letter
inspection with zero unresolved entries. Go taxonomy/company publication
completed at 15:23:05 UTC. The mode-0600 evidence log is
`/tmp/jobseek-go-board-queues-deploy-36328634446.log`.

Required CI and installed-image parity passed before merge; Crawler Deploy
Gate was green on the exact ready head. Supported c1 rollback retired epoch
76 at 77, restored five schedules, and left zero terminal drops or write
fences. A scheduled Go reconciliation held the host mutation lock afterward;
it finished with service exit zero before all 23 selectors were cleared.
Image promotion occurred only after that cleanup. The host wrapper is now
attested at the merged revision and invokes Go directly. Source draft #10092
is superseded by this deployment.

All four workers started Go reapers on the new image. An initial exporter
snapshot reported 129 successful document exports, zero errors and zero lag.
The 23 selectors were staged against the exact new full revision above.
Supported c1 activation reached **epoch 78**, with all services healthy. Its
conservation audit returned accepted/audit_ok: five ready records, zero
inflight or dead. A read-only comparison of all **7,879 enabled boards**
found zero differences in Redis versus PostgreSQL board URL, crawler type,
company ID, domain and monitor/detail browser flags. The helper is
`/tmp/jobseek-go-queue-db-proof.py`; it performs no mutations or origin calls.
A later exporter snapshot reported 230 successes, zero errors and zero lag.
Natural Go Workable monitor cycles remain pending. The active selector helper remains
`/tmp/jobseek-post-go-maintenance-selectors.py`, with Kandou URL
`https://kandou.bamboohr.com/careers/310`. Before the next deployment, roll c1
back, then clear all 23 selectors under the mutation lock against the actual
current revision. Never reuse the older 22-selector helper.

At the v0.13.871 checkpoint, CSV preparation and the local PostgreSQL
transaction were still Python. The v0.13.873 release above replaces them;
workers, remaining extraction profiles and final whole-lane evidence remain
open under #7966.

## Production checkpoint: 2026-09-27 — Go maintenance v0.13.870

PR #10089 merged as `ec892dc2899228e3a72526eb7d2f4cd5820445f3` and
[deploy 36326098454](https://github.com/colophon-group/jobseek/actions/runs/36326098454)
succeeded. Go schema setup and taxonomy/company sync completed in the actual
deploy: 37,526 location, 562 occupation, 36 seniority, 186 technology, and
6,039 company documents; zero company deletions. Go dead-letter inspection
reported 23 entries (20 actionable, three superseded, zero unresolved).

[Go taxonomy proof 36326995545](https://github.com/colophon-group/jobseek/actions/runs/36326995545)
passed with every static document and all active collection schemas matching.
The completed full backfill proof and resource limits are recorded in
[the production evidence](26-go-typesense-backfill-production-evidence.md).

Supported c1 rollback retired epoch 74 at epoch 75, restored all five
schedules, and left zero drops or write fences. The old 22 selectors were
cleared before merge. After deployment, 23 selectors were staged against the
new full revision, adding the four captured Workable boards documented in
[their evidence record](25-go-workable-production-evidence.md). Supported c1
activation reached epoch 76; all services are healthy. The conservation audit
returned accepted/audit_ok: five ready records, zero inflight or dead. Workers
run the Go lease-reaper child. A point-in-time exporter read reported 5,151
exported documents, zero document errors, and zero lag.

The first bounded Go reconciliation slice completed at 14:50:48 UTC through
the existing attested host launcher, which execs Go via the compatibility
CLI. Run `6f0ca3ad-8671-4291-b998-7f33f33eb8b9` checked 355,623 local and
remote rows across 16 partitions, repaired one difference, and left zero
unresolved. The launcher exited zero; its mode-0600 local log is
`/tmp/jobseek-go-reconciliation-ec892dc2.log`. All four workers reported Go
reapers without errors. The six original maintenance drafts were closed as
superseded by deployed #10089. Natural Go Workable cycles remain pending;
do not force them.

For that v0.13.870 release, mutation required supported `rollback c1`, then
clearing **23** selectors under the host lock with
`/tmp/jobseek-post-go-maintenance-selectors.py clear`, the actual deployed
full revision above, and `https://kandou.bamboohr.com/careers/310`. The older
22-selector helper no longer matches the live set. PR #10093 subsequently delivered the Go board-queue publisher, direct host
launcher and reaper pool-limit/budget correction; see the newer checkpoint
above. The full #7966 migration remains open.

## Implementation checkpoint: Go board sync queue publication

The next slice routes configuration sync's committed board schedules and
retired-board cleanup to `go-typesense-exporter --sync-board-queues`. Go owns
the existing Lua calls, board hash writes, provider delay keys, and bounded
1,000-board pipelines. No Redis publication occurs before the local database
transaction commits. Redis errors abort sync; ambiguous transport writes are
not automatically replayed. The child is bounded and reaped on cancellation.

The Redis fixture compares every stored key against the Python publisher,
including repeated execution, both worker types, first-time/recurring work,
existing leases, repair deadlines, rate/rotation floors, and corrupt state.
A 1,001-board case covers the batch boundary and stops later batches/removals
on failure. This implementation is deployed in v0.13.871. CSV/local PostgreSQL sync,
workers and remaining extraction/profile stages still require Go migration.

## Implementation record: Go configuration sync v0.13.873

The next configuration-sync port is on `fix-crawler/go-registry-sync`, created
from then-latest main `334f5631d` in an isolated worktree and subsequently
integrated with #10093 and latest main `012191b81` (#10095). The candidate
was subsequently deployed by #10096 as recorded above. Both `crawler sync` and the standalone
`python -m src.sync` entrypoint exec `go-typesense-exporter --sync-registry`.

Go now reads CSVs while preserving Python/Polars null versus quoted-empty
cells, BOMs and multiline CRLF; prepares canonical taxonomy, company and
description SQL arguments; computes board metadata and monitor fingerprints;
and reproduces browser/fallback routing and provider throttle hosts. The
retained Python functions generate the comparison evidence without publisher
traffic. Canonical SQL and registered route facts are embedded contracts.

Actual repository comparisons passed with the race detector:

- 6,013 companies and 5,876 descriptions, plus taxonomy tables: all CSV cells
  and all 14 prepared SQL calls match.
- 7,879 boards: metadata, fingerprints, monitor/detail browser decisions and
  throttle keys match. An additional 493 synthetic cases exercise registered
  types, fallback chains, numeric/Unicode settings, malformed configuration,
  and provider metadata/URL identity boundaries.

Local mode-0600 oracle artifacts are
`/tmp/jobseek-go-registry-repository-fixture.json` and
`/tmp/jobseek-go-registry-board-fixture.json`. Run the Go preparation tests with
`REGISTRY_TEST_FIXTURE` and `REGISTRY_BOARD_TEST_FIXTURE`, respectively.
The complete local transaction, board identity/rehome/recovery effects,
pending taxonomy resolution, installed read-only data-mount checks and
post-commit Redis/Typesense orchestration are now implemented in Go. Real
PostgreSQL fixtures verify stable IDs, recovery deadlines, runtime metadata,
posting rehomes, removal/reappearance, transaction rollback and ambiguous
commit acknowledgements. A real Redis fixture verifies no queue effects on
local failure. Index tests repair the covering index and prove lock release.
All 6,013 companies and 7,879 boards also committed successfully from the
actual repository into an isolated local PostgreSQL fixture, with no external
publication. Go matches 5,915 Python occupation-resolution samples. The
technology-miss query uses explicit integer casts to avoid ambiguous array
inference; existing populated fields and technology arrays remain intact.

The module race suite, vet and focused CLI tests passed. Required CI and the
installed-image checks passed before the supported rollout above. Python
remains the retained oracle; Go owns deployed local sync. The latest main #10095 crawler deployment 36330596574 was correctly
held by the active B0 receipt; production remains the healthy v0.13.871
checkpoint above. This candidate includes #10095 and must use the normal
cold rollback and exact 23-selector sequence. Do not bypass that guard.

## Historical release candidate: Go maintenance v0.13.870

The completed implementations from #10072, #10074, #10076, #10077, #10085 and
#10088 are assembled on `fix-crawler/go-maintenance-transition`, created from
latest main `52e8c7b6a`. The individual sections below record implementation
history; the unified candidate supersedes their separate release order.
This candidate was delivered by #10089 as recorded in the production checkpoint
above; the six source PRs have been retired. The following describes its
original release order, not another outstanding validation gate.

One crawler image now includes Go reconciliation, exact taxonomy verification,
schema setup, taxonomy/company publication and posting rename updates,
dead-letter inspection/recovery, and the supervised expired-lease loop. Shared
CLI conflicts retain every command. The protected maintenance chain invokes
Go backfill, Go reconciliation, then Go taxonomy verification under its existing
host lock; schema setup remains part of deployment and sync remains post-commit.
This keeps the existing completed backfill implementation and cold rollback
image, queue guards, cursor fences, credential scopes and publisher policies.

Release only after the already-running production proof 36317322087 finishes.
Then re-read live revision/receipt and exact PR checks, use supported c1
rollback, clear all 22 selectors under the mutation lock, promote the combined
candidate, stage at the new full revision and reactivate c1. Record normal
worker/reaper health, zero queue loss, Go schema/sync output, taxonomy parity,
CDC catch-up and the next bounded scheduled Go reconciliation result. The
systemd wrapper's separately attested direct-Go launcher follows **after** the
new binary is deployed; its current compatibility CLI already execs Go in the
candidate image.

CSV/local transaction sync, Redis board setup, worker claims/heartbeats and
processing, remaining monitor/detail/browser profiles, final whole-lane
resource/cost proof and retirement remain open under #7966. Do not equate this
maintenance release with completion of the full migration.

## Implementation slice: 2026-09-27 — Go reconciliation

The next release adds `go-typesense-exporter --reconcile`, replaces the
Python CLI runtime through `exec`, and invokes Go directly in the full
backfill maintenance chain. It retains the existing reconciliation ledger,
256-partition cursor, shared exporter fence, exact payload comparisons,
bounded complete-stream retries, repair/readback/source-stability checks,
legacy bucket cleanup, and durable candidate-order readiness receipts.
Bootstrap cleanup also holds the shared exporter fence through its local
absence checks and verification. No CDC cursor or exporter owner is changed.

The integration tests execute the actual state migrations against isolated
PostgreSQL schemas and exercise real HTTP imports/exports and cursor locks.
They prove that an ambiguous acknowledgement leaves the partition unadvanced,
a resumed repair converges, the full 256-partition cycle persists its evidence,
orphans are removed, cancellation records interruption, and competing owners
or stale in-memory receipts cannot establish a successful proof. Local
PostgreSQL 18, Go race/vet checks, the CLI/deployment tests, and workflow tests
passed. Production reconciliation remains on the previous runtime until this
release is deployed and its normal bounded run is observed.

The systemd wrapper has a separate installed SHA contract. Keep its current
`crawler reconcile` launcher for this image rollout; the new CLI immediately
execs Go. After this image is live, change the wrapper to invoke Go directly
through the supported reconciliation-host deployment. Installing that direct
wrapper before the binary exists would break the previous image. Taxonomy
verification, configuration sync, and other Python-owned stages remain on the
full migration backlog. The full #7966 gate is still open.

## Implementation checkpoint: 2026-09-27 — Go Typesense sync

`fix-crawler/go-typesense-taxonomy-sync` builds on taxonomy verification #10074
and implements the post-commit Typesense publication stage in Go. It is
implemented and locally verified, **not deployed**; release after the pending
reconciliation, verification, and schema-setup slices. The live protected
backfill proof still owns the host mutation lock.

The Go stage publishes complete location, occupation, seniority, technology,
and company documents from one static database snapshot. Company details
include all localized descriptions and industry names. Imports are bounded
to 1,000 documents. Exact company censuses preserve the acknowledgement,
missing-ID, duplicate/pagination, 50-document/1% deletion-budget, and final
convergence gates. Normal active/year counts come from the same Typesense
facets as the web and final refresh, avoiding initial seniority/technology
posting scans; local taxonomy counts remain bootstrap fallbacks. The final
Go refresh and typeahead invalidation are retained. Parent cancellation reaps
the Go child, and nonzero exit prevents a successful sync result.

Complete document parity matches the retained Python producer. A real
PostgreSQL + HTTP fixture executes the Go runtime through five full imports,
five count updates, twelve facet reads, and both company censuses. It has no
posting table, so passing proves that normal count reads stay in Typesense.
Prune-budget/failure, exact-pagination, import-bound, and child-lifecycle tests
also pass locally. CI and real production output/resource evidence remain
pending. Python still owns CSV/local transaction writes, Redis board effects, and
deadletter reporting; #7966 remains open.

The same sync slice now also owns pre-transaction name snapshots and posting
rename updates in Go. The before-map travels over a bounded stdin JSON handoff;
Go re-reads names under the exporter fence and processes affected postings in
1,000-row UUID keyset batches. Technology-name order/duplicates and existing
per-document rejection behavior are preserved. It never changes CDC cursor or
owner. The real PostgreSQL test covers 1,001 affected postings, an unaffected
row, null technology IDs, and fence release after ambiguous acknowledgement.
The Python snapshot/rename implementations remain offline references only.

## Implementation checkpoint: 2026-09-27 — Go taxonomy verification

The next release slice on `fix-crawler/go-typesense-taxonomy-verification`
ports `verify-typesense-taxonomies` to Go. It is implemented and locally
verified; **it has not been deployed or proven against production yet**.
Release it after reconciliation PR #10072 and after the active protected
backfill proof releases the host mutation lock.

The new runtime reads all five static taxonomy/company contracts in one
read-only repeatable-read PostgreSQL snapshot, checks six live schemas, and
compares every remote document with pages bounded to 250. It preserves exact
Python evidence hashes, missing versus null fields, localized aliases,
hierarchy membership, and redacted mismatch details. It fails on incomplete
or repeating pagination and emits one JSON record. Both the compatibility
CLI and protected maintenance route to Go; no Python verifier fallback is
used. Retained Python code is an offline oracle/cold-rollback reference.

Tests compare complete Go/Python evidence for ready and drifted collections;
exercise malformed/count-changing pagination, ambiguous numeric values,
hierarchy failures, schema differences, cancellation and error redaction;
and execute all nine SQL queries against PostgreSQL while another connection
commits a taxonomy update between reads. All Go race tests, nine focused
Python tests, 90 workflow tests, and seven web safety assertions pass locally.
CI, production verification, and resource evidence remain pending.
Configuration/taxonomy sync and schema setup are still Python production
work, so this slice does not satisfy the complete #7966 gate.

## Implementation checkpoint: 2026-09-27 — Go Typesense schema setup

`fix-crawler/go-typesense-schema-setup` implements the next deploy-time Python
owner in Go, intended after reconciliation #10072 and taxonomy verification
#10074. **This branch has not been deployed.** The production backfill proof
still holds the mutation lock; do not interrupt it to release this slice.

Deployment invokes `go-typesense-exporter --setup-schemas` directly using the
same Typesense-only credential scope and maintenance provenance. CLI/operator
wrappers exec Go. All seven collection definitions, aliases, search token
configuration, one-field index rebuilds, missing-field additions, explicit
force behavior, and memory-delta evidence are preserved. Ambiguous synchronous
PATCH timeouts are observed and re-read before retry; schema requests retain
the one-hour timeout and two-hour per-collection repair deadline.

Local Go race/vet tests pass, including existing-alias preservation,
concurrent collection creation, timeout-after-apply without replay,
busy-operation observation, missing status endpoint fallback, and bounded
cancellation. The embedded schema is compared with the retained Python source;
94 Python/schema/deployment tests and 90 workflow tests pass. CI and production
setup/readiness evidence are pending. CSV/configuration/taxonomy sync remains
Python and full #7966 completion remains open.

## Implemented next: Go expired-lease recovery (not deployed)

The scheduler's reaper loop now runs in a supervised Go process. It owns the
interval, both Redis lane sweeps and the dead-letter lifecycle join. The
remaining Python worker is a process/metrics adapter for this stage; it no
longer decides when or how expired leases recover. The canonical Lua and
publisher/queue policy are preserved. The frozen Python fixture checks exact
Redis state across 16 cases and three consecutive sweeps per case, including
batch bounds, unchanged due times, poison strikes, repair deadlines, missing
configs, ready tiers/rotation and the Lightpanda ownership guard. Real Redis
and PostgreSQL also exercise the complete sweep/dead-letter/classification
tick. Child cancellation, early exit, force-kill fallback and metric labels
are covered at the Python supervisor boundary.

This release remains queued behind the full maintenance proof and earlier Go
maintenance PRs. Do not deploy over the running mutation lock or claim this
completes worker scheduling, the full pipeline or #7966.

## Progress: 2026-09-27 13:40 UTC — backfill complete, proof running

The Go phase of [maintenance 36317322087](https://github.com/colophon-group/jobseek/actions/runs/36317322087)
finished at 13:27:38 UTC: **5,669,012 acknowledged documents in 5,472.035 seconds**.
The existing fresh full Python reconciliation then started in the same
container under the same mutation lock. At 13:36:35 UTC it had completed
partition `1f`, with zero unresolved differences in that partition. The full
256-partition proof and following taxonomy verification are still pending;
do not deploy over this process or declare final parity yet.

The final Go-phase cgroup sample at 13:27:37 UTC recorded 784.207 CPU-seconds,
79,636 KiB process RSS and 108,976 KiB process high-water RSS. Across 346
successful Go-phase samples the maximum sampled cgroup memory was 100,868,096
bytes. Sampling began after process startup and cgroup memory.peak is unavailable.
These numbers describe this maintenance container only; they do not prove
whole-lane efficiency or a comparison against the Python backfill.

Implementation PRs #10072 (reconciliation), #10074 (taxonomy verification),
#10076 (schema setup), and #10077 (taxonomy/company sync and posting rename
updates) are queued behind this proof. They are not deployed. The next
configuration-sync substage now implemented in Go is the read-only dead-letter
lifecycle join shared by sync, worker metrics and operator inspection. Its
Python-oracle fixtures and real PostgreSQL/read-only Redis integration cover
classification, batch boundaries, corrupt authority and membership preservation.
The same PR now implements explicit retry/prune in Go, including an atomic
Redis transition and a PostgreSQL row lock across the mutation boundary.
Tests prove due-time preservation, schedule deduplication, changed-config and
changed-authority refusal, superseded-inflight protection, and exact-member
replay refusal. These changes are not deployed. CSV/local transaction sync,
remaining scheduler/worker stages, and fleet-wide profile migration remain.

## Production checkpoint: 2026-09-27 — Go Typesense backfill

[PR #10069](https://github.com/colophon-group/jobseek/pull/10069) merged as
`fcb19acf39ab8f55687fa870de4e2e15dd21beb0`.
[Deployment 36316456325](https://github.com/colophon-group/jobseek/actions/runs/36316456325)
successfully promoted crawler v0.13.864. The operator backfill CLI now execs
`go-typesense-exporter --backfill`; the maintenance workflow invokes it
directly. The Go PostgreSQL integration fixture, race tests, installed-image
parity, and required CI passed before merge. A stale deployment-test command
expectation was corrected; the complete maintenance proof chain remains
under one host mutation lock and fails on any unsuccessful step.

Before deployment, supported c1 rollback retired epoch 72 at epoch 73,
restored all five schedules, and reported zero terminal drops or remaining
write fences. All 22 exact selectors were cleared under the mutation lock
against the old release snapshot. After promotion they were staged against
the new full revision above, and supported activation restored c1 at epoch
74. The queue conservation audit returned `accepted/audit_ok`: five ready
records, zero inflight, and zero dead. Workers, browser, drain, producer,
executor, claimant, and Redis are healthy; the live exporter still reports
the durable owner as `go`. No publisher due scores or duplicate origin
requests were forced.

The exact-revision full proof is running in
[maintenance 36317322087](https://github.com/colophon-group/jobseek/actions/runs/36317322087).
It runs Go backfill, then the existing full fresh Typesense reconciliation
and taxonomy verification. **Production backfill output and final index
parity remain pending until that run completes.** Read-only cgroup CPU and
memory samples are being captured to local mode-0600
`/tmp/jobseek-go-backfill-fcb19acf-resources.jsonl`; these describe the
maintenance container, not same-workload whole-lane efficiency or cost.

Before another crawler deployment or selector change, use the supported c1
rollback, then clear all 22 selectors under
`/run/lock/jobseek-crawler-mutation.lock` with
`/tmp/jobseek-post-go-sitemap-selectors.py clear`, full release revision
`fcb19acf39ab8f55687fa870de4e2e15dd21beb0`, and
`https://kandou.bamboohr.com/careers/310`. The running maintenance operation
also holds that lock; let it finish before another deployment. Never edit
the host environment manually.

The next implementation slice ports resumable reconciliation. Comparison and
bounded Typesense streaming helpers are checkpointed on
`fix-crawler/go-typesense-reconciliation` at `e94ed35e6`; they are not wired
into production. PostgreSQL repair, durable partition/run progress, CLI and
timer routing, and integrated failure/restart tests remain to implement.
Python reconciliation and taxonomy verification still run in production,
and the complete [#7966](https://github.com/colophon-group/jobseek/issues/7966)
gate remains open.

## Implementation slice: 2026-09-27 (before deployment)

Read-only inspection confirms that production already runs the Go Typesense
posting exporter, despite the older exporter section below. The host remains
at `f4520232f1f8c0905a68586dbfe4b736b7f17e79` with c1 epoch 72 active and
healthy. Scheduled count refreshes also invoke Go. The next runtime slice
ports full posting backfill to `go-typesense-exporter --backfill` and routes
both the operator CLI and maintenance workflow through it. It preserves the
shared cursor fence, commit-safe cutoff, full posting/company projection,
per-document acknowledgements, bounded retries, and cursor monotonicity.
The follow-on full reconciliation and taxonomy verification remain Python.

The backfill slice is not yet deployed or proven on production output. Its
real PostgreSQL fixture exercises fence exclusion, ambiguous acknowledgements,
restart/replay, and final cursor persistence. Before deploying, use the
supported c1 rollback and exact 22-selector cleanup described in the production
checkpoint below; restage and reactivate only against the promoted revision.
See the [Go Typesense runtime commands](../apps/crawler/go/typesense-exporter/README.md).

## Production checkpoint: 2026-09-25 20:22 UTC

This section supersedes the earlier pending Verity observation below.

[PR #10031](https://github.com/colophon-group/jobseek/pull/10031) fixed
Compose propagation of `SITEMAP_GO_BOARD_IDS` after the first v0.13.861
release left the selector absent inside workers. It merged as
`f4520232f1f8c0905a68586dbfe4b736b7f17e79`; [deploy run 36178705066](https://github.com/colophon-group/jobseek/actions/runs/36178705066)
successfully promoted v0.13.862. The ARM64 B0 fixture completed successfully;
this selector wiring does not change the Go sitemap parser.

Before deployment, the supported c1 rollback retired routing epoch 70 at 71,
restored all five schedules, and reported zero terminal drops and write
fences. The 22 exact selectors were cleared under the host mutation lock with
`/tmp/jobseek-post-go-sitemap-selectors.py`, the old full release revision,
and Kandou detail URL `https://kandou.bamboohr.com/careers/310`. After
promotion, the same 22 were staged against the exact new release snapshot and
c1 was reactivated at routing epoch 72 with five selected schedules. Worker1
now sees Verity's exact `SITEMAP_GO_BOARD_IDS` value
`c4779214-ef92-4261-98fb-ae64f264fd23`. Workers, browser, drain, producer,
executor, claimant, and Redis are all healthy.

The read-only epoch-72 queue conservation audit returned `accepted/audit_ok`:
five records, all ready, zero inflight or dead.

Verity's last Python monitor completed naturally at 19:12:41 UTC, before
activation. The first selected Go sitemap monitor completed on its normal
schedule at 20:21:21 UTC. It returned three URLs from one HTTP response
(911 bytes), with sorted URL SHA-256
`3427540e27b9a618103f26eb9a178000e5296b1b4db0f3e0e09cc9672acda288`.
The existing board writer persisted exactly three active PostgreSQL rows with
the same sorted digest; `last_success_at` advanced to 20:21:21 UTC, the next
check to 21:21:21 UTC, and `consecutive_failures` remained zero. No due score
or duplicate origin request was forced. The post-run epoch-72 queue audit
again returned `accepted/audit_ok`, five ready records, and zero inflight or
dead; all nine runtime services listed above remained healthy. This proves
one selected Go HTTP monitor and its database effects, not fleet-wide output
or whole-lane resource parity. Before another crawler deploy
or selector mutation, cold-rollback c1, then clear all 22 selectors under
`/run/lock/jobseek-crawler-mutation.lock` with the same script in `clear`
mode, exact release revision `f4520232f1f8c0905a68586dbfe4b736b7f17e79`,
and the Kandou URL above. Never manually edit `.env`.

The full [#7966](https://github.com/colophon-group/jobseek/issues/7966)
remains open. Complete and prove every enabled monitor, detail scraper, and
browser profile in Go HTTP/API or Go + Lightpanda; move scheduling, extraction,
enrichment, persistence, drain, export, maintenance, and sync from Python;
remove production Playwright and Chromium after profile parity; measure
same-actual-workload whole-lane CPU, peak and retained RAM, correct output
density, and attributable cost; and exercise final quiesced cutover and cold
reversal without queue loss, stale writes, or duplicate origin traffic.

## Production checkpoint: 2026-09-25 18:43 UTC

[PR #10029](https://github.com/colophon-group/jobseek/pull/10029) merged as
`bf1437d6c97bc3ffc50c7b36f13992640b0bd070`; [deploy run 36172829219](https://github.com/colophon-group/jobseek/actions/runs/36172829219)
promoted crawler v0.13.861. The bounded Go sitemap parser from the read-only
pilot is packaged in the crawler image and accepts only an explicit
same-origin HTTPS `urlset`. The adapter runs the same Python URL filter,
allowlist, and transform stages before the existing board writer. It is
default-off; an unsupported index or changed configuration fails closed. The
accepted [production shadow](https://github.com/colophon-group/jobseek/issues/8641)
previously matched Python's URL counts and hashes for Acosta/Dee Set and
Verity Breezy. Its exact board ID is
`c4779214-ef92-4261-98fb-ae64f264fd23`.

Before that deploy, the supported c1 cold rollback retired epoch 68 at epoch 69 and
restored all five schedules with zero terminal drops or write fences. The old
21 exact selectors were cleared under the host lock. After promotion, 22 exact
selectors were staged at `bf1437d6c97bc3ffc50c7b36f13992640b0bd070`
using `/tmp/jobseek-post-go-sitemap-selectors.py` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. Supported c1 activation selected
five schedules at epoch 70; workers, browser, drain, producer, executor,
claimant, and Redis are healthy. Post-activation inspection found that Compose
did not pass `SITEMAP_GO_BOARD_IDS` into workers, so Verity remains
Python-owned despite its host selector. The follow-up v0.13.862 release wires
that key. Before deploying it, cold-rollback c1 and clear all 22 exact
selectors with the same script, release revision, and Kandou URL under the
host mutation lock. After promotion, stage them again at the new exact
revision and reactivate c1. Then observe Verity's natural Go URL digest,
active PostgreSQL URLs, and board failure count; do not force its due time or
send a duplicate origin request. The full
[#7966](https://github.com/colophon-group/jobseek/issues/7966) completion
goal remains open while Python and Chromium own production work.

## Production checkpoint: 2026-09-25 15:16 UTC

[PR #10024](https://github.com/colophon-group/jobseek/pull/10024) merged as
`c09c1d519` and [deploy run 36148928512](https://github.com/colophon-group/jobseek/actions/runs/36148928512)
promoted crawler v0.13.858. The Go Workable detail scraper is installed but
defaults off. A naturally scheduled KI Insurance Python detail response was
captured without another origin request. Its 5,010 exact bytes, SHA-256
`0b7b1f982990b630b5b1f9ef51c28437b520761af8e849b5610ae6ac86b0f0fd`,
produced identical Python and Go values for all seven populated detail fields.
The Python scrape succeeded. KI Insurance has 18 active jobs, zero board
failures, and no browser requirement.

The supported c1 rollback retired epoch 65, restored all five schedules,
and confirmed zero dropped tasks and write fences. Twenty exact selectors
were cleared under the mutation lock; 21 were staged at exact release
`c09c1d5191f779280e088c6b6131a1c66426835f` with
`/tmp/jobseek-post-workable-go-detail-pilot-selectors.py` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. C1 reactivated at epoch 66 with
all five schedules; services are healthy. The added Workable Go detail selector
targets only KI Insurance board `aef95fd2-55ea-43cd-9aa6-9bd541c2bc4e`.
Its natural Go scrape and persisted content readback are pending. The list
capture selector still targets four Python Workable boards; natural scheduled
responses are next due around 15:40–15:57 UTC. No due scores or extra origin
requests were forced. Personio 5% selects Silverflow and newly eligible
Eraneos Germany (five stable 21-job runs); Eraneos Go output is pending.
Before any further deploy or selector mutation, cold-rollback c1 and clear
those exact 21 selectors with that script, revision, and Kandou URL.

The next strict Lever code slice resolves five of the six remaining Python
boards: two explicit dotted tokens, two explicit tokens on canonical company
career URLs, and one unused `company` metadata value matching its derived
token. The sixth, Volta Medical, retains a JSON-LD detail scraper and remains
Python-owned. Live output and database effects for the five new routes remain
to be observed after deployment; this change cannot establish the full
[#7966](https://github.com/colophon-group/jobseek/issues/7966) gate while
Python and Chromium still own production work.

## Production checkpoint: 2026-09-25 14:26 UTC

[PR #10023](https://github.com/colophon-group/jobseek/pull/10023) merged as
`6c993efe5` and [deploy run 36145740751](https://github.com/colophon-group/jobseek/actions/runs/36145740751)
promoted crawler v0.13.857. The route census changed from 165 to 189 Go
Lever boards out of 195 enabled: 20 canonical direct URL boards now derive
their missing token as Python does, and four EU boards now derive the missing
region as Python does. Six Lever boards retain Python routing because their
configuration does not satisfy the strict Go guard. All 24 newly eligible
boards had zero consecutive failures before their first natural selected Go
cycle; live output and database readback for this cohort are still pending.
`LEVER_GO_PERCENT=0` reverses the default.

Supported B0 c1 cold rollback retired epoch 61 and restored all five retained
schedules before deployment. The 18 old selectors were cleared under the host
mutation lock. After deployment, the same 18 were staged with
`/tmp/jobseek-post-workable-capture-selectors.py` at revision
`6c993efe5780e38ed5f90730dbe881f52c485501` and Kandou URL
`https://kandou.bamboohr.com/careers/310`; c1 reactivated with five
schedules at epoch 62. Workers, browser, drain, producer, executor, claimant,
and Redis are healthy. Before another crawler deployment, cold-rollback c1
and clear these exact selectors with the same script, revision, and URL.
Workable list capture on natural Python schedules remains pending; do not
force due scores or duplicate origin traffic. C2 Kandou stays dark.

[Draft PR #10024](https://github.com/colophon-group/jobseek/pull/10024)
adds default-off Go Workable detail extraction and passive capture for exact
scheduled detail jobs and Workable Markdown/public API list fallback bodies.
It is not deployed or selected. The full [#7966](https://github.com/colophon-group/jobseek/issues/7966)
completion gate remains unmet while Python and Chromium own production work.

## Production checkpoint: 2026-09-25 13:41 UTC

[PR #10022](https://github.com/colophon-group/jobseek/pull/10022) merged as
`6c8b7179b` and [deploy run 36140762256](https://github.com/colophon-group/jobseek/actions/runs/36140762256)
promoted crawler v0.13.856. It adds a default-off Go Workable list monitor
and passive capture of existing Python list responses. Before deployment,
supported c1 cold rollback retired epoch 59 and restored all five retained
schedules; the 17 exact old selectors were cleared under the host mutation
lock. After deployment, 18 exact selectors were staged with
`/tmp/jobseek-post-workable-capture-selectors.py` at revision
`6c8b7179b91265336d25cee32275ea8f1bebbb9a` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. The added selector captures
Workable responses for `pix4d,debiopharm,unit8,hack-the-box-ltd` on their
natural Python runs. No Workable board routes to Go yet. Supported c1
reactivation selected five schedules at epoch 60; workers, browser, drain,
producer, executor, claimant, and Redis are healthy. C2 Kandou stays dark.
Before any later crawler deployment, cold-rollback c1 and clear all 18
selectors with that exact script, revision, and Kandou URL.

Silverflow Personio completed its first natural selected Go monitor run at
13:24 UTC: four URLs, two responses, zero monitor or board failures. Its
sorted URL digest
`2a7048973d0c0e572788718e93234a4e1a10d8c8b21ad43a76f6aff49960a1ba`
matched all four active PostgreSQL rows after the existing writer ran. The
earlier exact-byte EN/DE replay matched all four full rich dictionaries.
This is a live monitor and persistence checkpoint, not a whole-lane resource
measurement. The first Mobiliar SuccessFactors Go RSS run is still queued;
do not force it or duplicate origin traffic.

[PR #10023](https://github.com/colophon-group/jobseek/pull/10023) is open
from the new main. It lets the Go Lever runtime derive token and EU region
from a canonical direct board URL just as Python already does. Twenty current
CSV boards have this strict tokenless shape. Merge it only after its gates
pass and after preserving the B0 cold rollback / selector procedure above.
The fleet remains largely Python-owned and does not satisfy
[#7966](https://github.com/colophon-group/jobseek/issues/7966).

## Production checkpoint: 2026-09-25 12:48 UTC

[PR #10021](https://github.com/colophon-group/jobseek/pull/10021) merged as
`e9a1421d4` and [deploy run 36134079773](https://github.com/colophon-group/jobseek/actions/runs/36134079773)
successfully promoted crawler v0.13.855. Strict direct Lever boards now use Go
by default; `LEVER_GO_PERCENT=0` remains the route reversal. Supported B0 c1
was cold-rolled back at epoch 57, restoring all five retained schedules with
no loss, then reactivated at epoch 58 after deployment. C2 Kandou stays dark.
All workers, browser, drain, producer, executor, claimant, and Redis were
healthy after the transition.

Seventeen temporary selectors were staged under the host mutation lock using
`/tmp/jobseek-post-lever-default-selectors.py` with exact revision `e9a1421d4`
and Kandou rendered-DOM capture URL `/careers/310`. The route census is 1,471
Go selections and 6,394 Python selections across 7,865 enabled boards. Before
another crawler deployment, cold-rollback c1 and clear those exact selectors
with the same script, full revision and Kandou URL; then stage the desired
post-release selectors and reactivate c1. The prior 20 natural Go
Greenhouse/Lever/Workday cycles had zero failures and exact active PostgreSQL
URL counts and digests. Their rich fields and whole-lane resource efficiency
were not established by that readback.

Silverflow Personio's natural Python monitor captured EN and DE XML at 12:18
UTC. Offline Go and Python replay of the exact same bytes matched all four
ordered rich-job dictionaries, including descriptions and German
localizations. The four active PostgreSQL URLs matched digest
`2a7048973d0c0e572788718e93234a4e1a10d8c8b21ad43a76f6aff49960a1ba`,
with zero board failures. Its exact Go selector is now active; the first
natural Go cycle is due after 13:18 UTC. The first Mobiliar SuccessFactors
Go RSS cycle remains queued. The Go Workable URL monitor stays default-off
pending live response capture and admission; its existing detail scraper
still runs in Python. None of these steps closes [#7966](https://github.com/colophon-group/jobseek/issues/7966).

## Production checkpoint: 2026-09-25 12:00 UTC

The latest crawler release is `4c620132c` (v0.13.854), deployed by
[run 36128889906](https://github.com/colophon-group/jobseek/actions/runs/36128889906).
Supported B0 c1 is active at epoch 56 with five retained schedules; c2
Kandou remains dark. Temporary selectors route 1,470 of 7,865 enabled boards
to Go monitors: 1,275 Greenhouse, 165 Lever, 21 Ashby, and nine individually
selected boards. Fourteen natural Greenhouse/Lever batch cycles since 11:43
completed with zero failures and exact active database URL count/digest
readback. This checks persistence of the selected URL inventory, not every
rich field or the whole crawler lane. The same-byte Mobiliar SuccessFactors
RSS replay matched all 71 rich jobs before its exact Go selector was staged;
its first natural Go cycle remains pending. A Silverflow Personio response is
being captured passively on its next normal schedule. Do not force either
board due or send a second request to its origin.

The next code release makes the strict 165-board Lever cohort Go by default,
while `LEVER_GO_PERCENT=0` remains the immediate route reversal. Before any
crawler deployment, use the supported B0 c1 cold rollback, clear temporary
host selectors under the mutation lock, and verify the host environment
matches the release snapshot. After deployment, stage desired selectors and
reactivate c1. Python still owns most monitors and the board writer; this
checkpoint does not satisfy [#7966](https://github.com/colophon-group/jobseek/issues/7966).

The delivery goal is a complete Go crawler using self-hosted Lightpanda for
browser work and Go HTTP/API execution where that removes a browser need.
Python, Playwright, and Chromium leave production after every enabled profile
has equivalent output and the Go runtime owns scheduling, persistence, drain,
export, maintenance, and configuration sync. Temporary Chromium assignments
remain explicit during migration; eliminating them is a completion task, not
an admission condition for the first B0 cohort.

## Production checkpoint: 2026-09-24 20:20 UTC

This checkpoint supersedes the deployment and owner statements below. The
latest code baseline for further work is `origin/main` `3e9e737c4`.
[PR #9991](https://github.com/colophon-group/jobseek/pull/9991) merged the
guarded Go Typesense CDC exporter as `2ade431d9` and
[deploy run 36052100515](https://github.com/colophon-group/jobseek/actions/runs/36052100515)
successfully promoted that revision. The first deploy attempt failed before
the image changed because a temporary Elastic capture variable in the host
environment differed from the committed release. Its rollback restored the
old image and workers; the successful retry ran with both temporary pilot
variables removed. Clear pilot variables **entirely** before each future
deploy and check host environment attestation, excluding only `COMPOSE_FILE`.

The fenced production `python -> go` Typesense owner transfer succeeded at
about 20:13 UTC. Python exited on its ownership check, and the same exporter
service restarted under the Go entrypoint. At 20:19 UTC, Go had acknowledged
566 live documents with zero rejected documents, export errors, or unknown CDC
writers. The Typesense downstream availability metric was healthy, the process
was running without an OOM, and its cgroup used about 57 MiB of its 256 MiB
limit. The owner query returned `go`. This is a live ownership and correctness
checkpoint, not an equal-workload resource comparison for the whole crawler.
Keep the Go owner unless a concrete failure requires the documented reverse
transfer; never rewind the cursor.

The supported c1 rollback before deployment restored five due schedules to
Python at retired epoch 27. After the successful deploy, the one-board
Elevance Workday Go selector was staged again and confirmed inside a recreated
HTTP worker. The supported B0 c1 activation selected five boards at routing
epoch **28** and wrote `/home/deploy/.lightpanda-b0-active-v1` for the
`2ade431d9` image. Producer, executor, claimant, workers, browser, and drain
were healthy; the host mutation lock was free. C1's first retained detail is
due 2026-09-25 00:26 UTC. C2 remains dark because the Kandou required-title
mismatch has not been resolved. The Elastic natural response capture has not
arrived; its temporary capture variable is currently absent. Before another
crawler deploy, cold-rollback c1 with the supported wrapper, remove the
`WORKDAY_GO_BOARD_ID` line under the host mutation lock, and verify release
environment attestation. Reactivate c1 only after that deploy succeeds.

The completion gate remains [#7966](https://github.com/colophon-group/jobseek/issues/7966):
most enabled boards still use the Python worker and Chromium routes. Next,
observe c1's retained natural detail, continue the exclusive Workday monitor
cohort with same-input resource evidence, resolve a concrete Lightpanda
capability or Go HTTP profile, and replace the remaining Python runtime stages
and board families. Do not infer whole-fleet efficiency from these pilots.

## Execution checkpoint: 2026-09-24

This checkpoint supersedes the older epoch and pending-run statements below.
On the prior deployed revision `1f8e9113bfc4730ca9ba9ada183dbdd507827fa4`,
two natural Elevance Go Workday cycles completed at 16:18 and 17:25 UTC.
They found 308 and 309 URLs in 16 requests each with zero transport errors.
Both cycles' sorted URL digests matched their exact database readbacks after
the existing Python board writer ran. The publisher changed between cycles;
these are live persistence results, not same-input Python-versus-Go resource
comparisons. [#7955](https://github.com/colophon-group/jobseek/issues/7955#issuecomment-5818925063)
records the second run.

[PR #9989](https://github.com/colophon-group/jobseek/pull/9989) merged as
`d0077bdc3459c5f2380caae8e55b33a387b811d3` and
[deployed successfully](https://github.com/colophon-group/jobseek/actions/runs/36036386828).
It combines the default-off rich Elastic Go Greenhouse monitor, one scheduled
Elastic response capture, Kandou rendered-DOM capture, Booking main-response
and DOM capture, and the c2 legacy scrape-enqueue owner guard. Earlier draft
PRs #9979, #9987, and #9944 were closed as superseded. Synthetic same-byte
Greenhouse Go/Python field replay passes; live Elastic capture and whole-lane
measurement still gate exclusive Go activation. Kandou's intermittent
required-title failure keeps c2 dark.

For this deploy, supported c1 cold rollback retired epoch 25 and restored all
five future due schedules to Python before the crawler image changed. The
temporary one-board Workday selector line was removed entirely under the host
mutation lock. After deployment, the one-board selector and Elastic capture
path were staged under that lock, and supported c1 reactivation transferred
the same five due scores to Go at routing epoch **26**. The active receipt,
producer owner, and route agree; no B0 job is inflight or dead, all services
are healthy, and the mutation lock is free. The first existing c1 detail is
due 2026-09-25 00:26 UTC, so live Lightpanda detail output and production
whole-lane resources remain unmeasured. Remove the Workday selector line
entirely before any later crawler deploy, and cold-rollback c1 first.
[#8648](https://github.com/colophon-group/jobseek/issues/8648#issuecomment-5819378524)
records the transition. The fleet remains Python-owned outside the named
pilots; zero Python and zero Chromium are not achieved.

At 18:30 UTC a third natural Elevance Go Workday cycle found 311 URLs in 16
requests with zero transport errors. Its sorted URL digest
`5eb7dd7d88ec5730e840c720acb475f094106b3aa72c0b2b772165c9a62aa099`
matched the exact 311 active PostgreSQL rows. Three new details were
scraped successfully by the existing Python detail owner. The publisher
changed again, so this remains output/persistence evidence for the Go list
monitor rather than a whole-lane resource comparison.

## Go Typesense exporter slice

An isolated branch based on deployed `d0077bdc` now contains a default-dark
Go Typesense CDC exporter. It uses the current Python export cursor encoding,
exact CDC cutoff SQL, shared PostgreSQL advisory fence, taxonomy inputs,
company JOIN, per-document Typesense acknowledgements, and bounded downstream
backoff. It checks a durable `export_owner:typesense:job_posting` row inside
the fence before every import. A missing row means Python; Python checks the
same row inside the same fence. Neither runtime can import under the other's
ownership. The owner-aware exporter entrypoint chooses the process at each
container start, including after an ordinary deployment rewrites `.env`.
The Go process retains the existing exporter staleness, Typesense health,
CDC cutoff, error, and bounded Redis queue-depth metrics on port 9093.

The production Python exporter is still authoritative. A read-only snapshot
of 200 recent production postings on 24 September projected identical
Python and Go Typesense documents across all fields. Go loaded its own live
taxonomy maps and read the exact same database cutoff as Python. One
occupation ancestor array initially differed only in order; both runtimes
now emit stable sorted order, and the 200-row rerun matched. The comparison
ran in a separate, memory-bounded container and sent no publisher or Typesense
requests. The production exporter restarted once after an initial comparison
attempt exceeded its 256 MiB container limit; it recovered and resumed
exporting. Do not run this comparison inside the live exporter again. The
Go writer has not imported a production document or advanced the cursor.

The handoff command is an explicit compare-and-swap:

```bash
cd /home/deploy
flock -x /run/lock/jobseek-crawler-mutation.lock \
  docker compose run --rm --no-deps \
  -e GO_TYPESENSE_EXPORTER_TRANSFER=1 exporter \
  /usr/local/bin/go-typesense-exporter --transfer-owner python go
```

Run it only after deploying the owner-aware image with Python still selected,
checking the cursor and index lag, and holding the host crawler mutation
lock. The command waits for the in-flight Python tick through the shared
fence, verifies the existing cursor, and records Go ownership. The Python
process exits on its next ownership check; Compose restarts the exporter
service, whose entrypoint selects Go. Confirm the process, port 9093 metrics,
cursor progress, acknowledgements, index lag, and absence of document drops.
If the Go process fails or those checks fail, reverse ownership with the same
command ending `--transfer-owner go python`; the Go process exits and the
entrypoint restarts Python from the retained cursor. Never rewind the cursor.
Before the first owner-aware image deploy, cold-rollback c1 and remove the
temporary Workday selector line from the host environment as described above;
reactivate c1 only after the new release passes its normal deployment gates.

## Current state

- [#7935](https://github.com/colophon-group/jobseek/issues/7935) reset the
  initial effort to bounded, independently useful pilots. The 10M-board
  projection and first-pilot gates in
  [the older migration design](23-go-lightpanda-migration.md) are historical.
  The owner subsequently made complete retirement of Python, Playwright, and
  Chromium the delivery goal in [#7966](https://github.com/colophon-group/jobseek/issues/7966).
  Its enabled-fleet and whole-lane completion criteria govern final cutover.
- The bounded Go sitemap worker and read-only production shadow landed through
  [#8644](https://github.com/colophon-group/jobseek/pull/8644); the fleet
  benchmark landed through [#8660](https://github.com/colophon-group/jobseek/pull/8660)
  and [#8694](https://github.com/colophon-group/jobseek/pull/8694). Python
  still owns sitemap schedules and writes. The earlier
  [#8461](https://github.com/colophon-group/jobseek/pull/8461) and
  [#8524](https://github.com/colophon-group/jobseek/pull/8524) drafts are
  closed without merge; they are evidence, not candidate branches.
- [#8881](https://github.com/colophon-group/jobseek/pull/8881) merged the
  bounded B0 Go supervisor, Lightpanda renderer route, Python database-only
  executor, and explicit c1/c4 cutover tooling. Ordinary deployment is dark.
  Production c1 uses the separate, receipt-guarded activation overlay.
- [#8738](https://github.com/colophon-group/jobseek/issues/8738) landed the
  bounded Go producer authority through [#9874](https://github.com/colophon-group/jobseek/pull/9874).
  The 48-file predecessor at `d6d583515` was reassessed against concrete
  crash and ownership failures and reconciled with current queue semantics.
  About half of its addition was test code. The exact merged revision passed
  required CI and the crawler deploy gate; [deploy run 35845382807](https://github.com/colophon-group/jobseek/actions/runs/35845382807)
  succeeded with only the dark claimant in the ordinary service list. There is
  no evidence of enabled B0 traffic in that deployment.
- [#8648](https://github.com/colophon-group/jobseek/issues/8648) owns the
  remaining whole-lane admission. [PR #9880](https://github.com/colophon-group/jobseek/pull/9880)
  merged the real-producer harness. Its exact-source [ARM64 fixture run](https://github.com/colophon-group/jobseek/actions/runs/35861227140)
  exercised the real Go producer in all 16 arms with exact output, request,
  persistence, and queue parity. Median correct-URL density improved 3.42×
  at c1 and 6.71× at c4; peak memory and completion latency fell. CPU use
  rose from 12.88 to 23.06 seconds at c1 and 17.53 to 23.88 seconds at c4
  across the common due and retained measurement window. About 20 CPU seconds
  per arm came from the transitional Python executor. Production c1 reached
  routing epoch 14 with five Go-owned schedules. Its lightweight executor
  health probe cut measured idle CPU from 10 to 1 seconds per 20-second
  window. A cold rollback returned all five schedules to the legacy queue
  before [#9934's crawler deploy](https://github.com/colophon-group/jobseek/actions/runs/35903707755);
  the supported cutover reactivated c1 at epoch 16 afterward. For the
  [Go drain deploy](https://github.com/colophon-group/jobseek/actions/runs/35912138580),
  c1 was cold-rolled back again at retired epoch 17, with all five exact due
  scores restored, then reactivated on revision `4a88deaf` at epoch 18.
  Production B0 output, requests, and whole-lane capacity still need
  measurement. The public sitemap run in #7935 was inconclusive because the live source changed
  between arms; it is not a Python-versus-Go verdict.
- [#7959](https://github.com/colophon-group/jobseek/issues/7959) admitted
  pinned Lightpanda 0.4.0 for narrow B0/B1 nonproduction use. Its broader
  compatibility results do not justify moving interactive, frame, identity,
  or API-sniffer profiles. Chromium remains an explicit compatibility owner
  where those capabilities are unproven.
- [PR #9932](https://github.com/colophon-group/jobseek/pull/9932) merged and
  deployed the exclusive Go R2 drain. A counterbalanced 2,000-description
  ARM64 fixture preserved every object and database pointer while reducing
  CPU 3.75–3.86× and retained memory 9.5–10.1×. A
  [200-second production sample](https://github.com/colophon-group/jobseek/issues/7945#issuecomment-5802172400)
  observed 247 successful uploads, 17.72 ms CPU per upload, 21.9 MiB final
  cgroup memory, and exact R2/DB/pointer readback on five samples. The prior natural
  Python sample used 22.14 ms CPU per upload and about 118 MiB memory; the
  input sizes differed, so only the fixture is an equal-input CPU comparison.
  [PR #9935](https://github.com/colophon-group/jobseek/pull/9935) prepares
  the three validated production B0 origins; it remains undeployed pending
  c1's first due work.

## Next slice: one real Go-owned B0 cohort

1. **Producer implementation landed.** The producer successor was
   assembled separately from the held #8648 admission harness. It keeps only
   the bounded producer, four Go claim slots, existing Redis/SQL fences,
   the Python database-only executor, and cold rollback. The large patch
   addressed specific crash and ownership failures; it is not a template for
   later family ports.
2. **Keep authority and reversal correct before traffic.** A selected posting
   must have exactly one schedule and one owner. The existing routing epoch
   must be monotonic across activation and rollback, including restored Redis
   or host snapshots. Cover lease loss, failed render/executor transitions,
   Redis persistence boundaries, pending-receipt reboot, and rollback from
  current PostgreSQL schedule truth. Bound task occupancy for the next cohort;
   do not build generic compaction or a new distributed scheduler for it.
3. **Dark default; c1 overlay is receipt guarded.** The merged revision passed real
   Redis/PostgreSQL transitions, container startup, and the required CI/deploy
   gates. The ordinary deploy lists the old Python/Chromium services and dark
   claimant. The c1 overlay has been cold-rolled back and reactivated around
   crawler deploys without changing its five retained due times. The current
   active epoch is 18 on revision `4a88deaf`. Check the
   current receipt, Redis owner, and host mutation lock before any mutation;
   [#8648](https://github.com/colophon-group/jobseek/issues/8648) records the
   current routing epoch. No reviewer or operator approval is an additional
   gate once the stated checks pass.
4. **Whole-lane fixture complete.** The merged harness exercised the producer
   with identical immutable fixture inputs and equal 1.5 GiB whole-lane
   budgets. Its report includes exact canonical output, terminal state,
   requests, queue conservation, paired CPU seconds, peak/retained RSS,
   elapsed time, and correct terminal URLs per GiB-minute. The measured
   density improvement and identical output meet the fixture decision rule;
   no arbitrary 1.25x hurdle or approval is required. Small live-source
   parity and production host telemetry remain part of c1 admission. Treat
   live-input drift as inconclusive.

   The held `fix-crawler/go-b0-admission` branch at `266725d45` is source
   material, not a merge candidate. It bypassed the Go producer via Python
   queue mutation. The merged harness retained its immutable fixture, PKI,
   output comparison, and external cgroup sampler, then fed the candidate
   through the Go producer's Unix socket. It counted the producer in the
   candidate cgroup and budget: 32 MiB producer,
   96 MiB supervisor, 384 MiB executor, 1024 MiB renderer (1536 MiB total),
   against a 1536 MiB control. The isolated counterbalanced c1/c4 report
   passed. This is fixture evidence; production c1 has not processed a due
   task yet.
5. **Observe c1, then expand to three origins if the numbers hold.** C1 has
   exclusive Go/Lightpanda ownership for one low-rate origin.
   Verify no duplicate origin request, stale write, lost schedule, unsupported
   capability, or same-task Chromium fallback. Expand to the other two
   currently validated origins only if c1 remains correct and the combined
   lane stays inside its memory and freshness limits. Judge complete scheduled
   work, exact output and queue effects, request conservation, CPU, and memory
   on the actual cohort. Record sample size and uncertainty; cold-rollback on
   a material regression. The
   fixed c4 benchmark manifest contains a `suspect` board. Production cutover
   uses a separate c3 manifest with only the three validated origins; c4 stays
   available to the frozen four-origin fixture and is rejected by the production
   activation wrapper. Never activate the suspect board just to reach four.

## Decision after B0

If the admitted multi-origin lane preserves output and uses fewer whole-lane
resources under the equal workload, choose one additional bounded family. The
Go HTTP sitemap worker already on `main` is the natural candidate, subject to
a stable-source cohort and the same exclusive ownership
and publisher-policy checks. Port only the semantics required by that cohort.
If the B0 result is weak or inconclusive, keep the working Python path and
resolve the measured limitation before expanding.

After B0, migrate one measured family or independently replaceable process at
a time: Go HTTP monitors and detail fetches, remaining monitor/scraper
families and enrichment, export/maintenance, then configuration sync.
For browser profiles, use pinned Lightpanda replay or move the origin to a
proved HTTP/API route. Track every temporary Chromium assignment until none
remain. Complete the migration only when the enabled fleet has zero Python or
Chromium crawl ownership, parity and resource measurements pass for its actual
workload, and the legacy production services and rollback window are retired.

Do not make the old broad issue list a dependency graph again. In particular,
defer catalog/outbox replacement, queue v2, dynamic sharding, generic browser
APIs, every ATS/monitor family, and Chromium retirement as *early pilot
prerequisites*. Revisit a Lightpanda capability when a specific configured
profile and pinned Linux build provide a concrete reason and exact replay
evidence. A new abstraction is justified only by a measured migration need.

## Issue ownership

- #7935: concise epic and the current decision record.
- #8738: completed implementation of exclusive Go B0 producer/ownership and
  cold reversal. #8881 is the merged dark foundation; #9874 merged the
  producer and deployed dark. Traffic admission remains #8648.
- #8648: whole-lane output parity and measured resource efficiency, followed
  by c1 observation and the validated three-origin expansion.
- #7941: cohort switch/reversal tracking folded into #8738; avoid a parallel
  generic cutover program.
- #7938: parked queue-v2 contract; it does not gate B0.
- #7962/#7963 and other family-port issues: deferred until measured cohort
  demand justifies a bounded slice.
- #7966: documented final zero-Python/Playwright/Chromium completion and
  retirement requirements. The owner closed the issue on 2026-09-27; the
  production migration still has the outstanding work recorded above.

## Implementation: Go experience extraction, v0.13.876

The shared resident Go enrichment process now also implements experience
requirements for monitor/detail CPU helpers. The production checkpoint above
records its successful deployment and natural persistence readback.
It preserves mixed-unit, forward and reversed rule ordering, numeric boundaries,
Unicode matching, highest-minimum/tie behavior, false-positive context and
exact half-up month rounding. Python remains the explicit rollback engine;
there is no runtime fallback.

The native and installed-protocol fixtures cover 1,967 Python oracle inputs;
43 focused bridge/CPU/experience tests and Go race/vet checks pass. The same
256 stored production postings match, with 66 non-null experience results.
A counterbalanced local replay of 2,560 completions per arm uses about 21%
less experience-stage CPU including IPC and the native child. Numeric output
hashes match exactly. This does not establish whole-lane resource savings.
See [the replay evidence](evidence/go-experience-replay-2026-09-27.json).
The v0.13.875 production record is preserved in #10103. For subsequent
releases, use the newest production checkpoint's rollback and selector protocol.

## Implementation: remaining Ashby monitor configurations, v0.13.877

The Go Ashby monitor now covers the remaining enabled configuration forms:
explicit tokens with internal spaces/dots, exact direct-URL token derivation,
custom career domains with explicit tokens, matching legacy `org`/`board_token`
metadata, writer-owned `blast_radius_floor`, and separate JSON-LD detail
configuration. Python token precedence and the single encoded API endpoint
are preserved. Unknown monitor/transport settings remain outside the direct
route. No board configuration, detail policy or delisting policy is changed.

A read-only snapshot of all 935 enabled Ashby monitors admits all 935 with
`ASHBY_GO_PERCENT=100`, up from 878 in v0.13.876. This projects 3,682 Go / 4,197
Python monitors across the unchanged 7,879-board registry. The production
checkpoint above now confirms those counts. Nord Security's configured JSON-LD detail path
remains a separate Python/browser migration obligation.

The 57 newly covered configurations have frozen Python/httpx endpoint fixtures;
all match the native binary, including percent-encoded spaces. Installed-image
CI repeats these checks with networking disabled. Focused Python/Go tests cover
request equivalence, rich output, routing rejection and preserved writer/detail
ownership. See [the native module contract](../apps/crawler/go/ashby-monitor/README.md).
Use the latest production checkpoint's 25-selector helper and supported cold
rollback before subsequent deployments.
