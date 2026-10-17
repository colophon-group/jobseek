# Go and Lightpanda serving checkpoint — October 17, 2026

Full migration remains **active and incomplete**. Release **0.13.1081** is serving
**7,842 monitors / 7,862 details** at epoch **368**, preserving the entire previous
7,840/7,859 cohort. The remaining captured frontier, excluding three B0 boards,
is **55 monitors / 35 details**. Unqualified assignments retain their legacy owners.

## Installed and independently verified

[PR #10665](https://github.com/colophon-group/jobseek/pull/10665) merged as
`2ba9c62a977307d1e8d5299ca5defbf24ee902ce`, with an identical whole repository
tree to qualified head `36459bec8df50c33b51ead19090ce08ba12556eb`. Complete
Required CI, native runtime contracts and the actual Crawler Deploy Gate pass.
Lightpanda **1.0.1**, released October 15, is the latest stable release verified
October 17 at 14:14 UTC. See the [official release](https://github.com/lightpanda-io/browser/releases/tag/1.0.1).

Both original immutable deployment workflows succeeded. The first renderer
attempt stopped on a cgroup memory attestation failure and left the renderer
cold; a fresh original retry succeeded. Independent readback confirms the
installed renderer and its 1 GiB memory limit with zero swap. The first failure's
cause beyond that guard remains unproved.

Supported B0 and ordinary staging/activation succeeded. The complete staged
plan contains **15,704 members**, including all five new original full-config
bindings. Ordinary activation completed at **13:35:08 UTC** after natural lease
settlement and supported cold projection repair. One outer attempt stopped
before invoking the original activation wrapper because an admin oneoff was
active. Independent source, images, receipts, SQL/Redis ownership, all ten actors
and health checks pass. No production SQL, Redis, lease or receipt was manually
patched. [Aggregate evidence](evidence/go-native1081-serving-checkpoint-2026-10-17.json)
retains exact source, workflow IDs, counts and private evidence digests.

## Queue failure, repair and current conservation

The previous source1080 observation captured **9,239 scheduled unqueued IDs**;
9,163 were currently eligible and lacked descriptions. Supported ordinary
retirement still left 9,132 eligible and unqueued. Preserve that failure evidence
under [issue #10693](https://github.com/colophon-group/jobseek/issues/10693).

Release1081 uses the existing **64-item fenced publisher** for ordinary and
streamed URL-only monitor results. Real 513-item Workday and SmartRecruiters
new-job and lost-enqueue regression cases pass: ownership/cache hash reads fall
from 1,558 to 46, retaining canonical stubs, first-time queues and settlement.
Focused race suites pass **31 queue / 38 worker** nodes with zero fixture skips.

The full postactivation replay checks **1,742,711 actual owned scheduled URLs**:
all are captured queued, with **zero owned cache mismatches or captured gaps**.
Compared with the previous queue capture, 1,727,840 IDs remain, 34,488 are
outgoing-only and 14,871 incoming-only. Current canonical and board/posting
publisher-policy checks classify every outgoing-only ID as **34,104 inactive /
384 unscheduled**; none is missing or eligible and unqueued.

All **9,239 original gap IDs** are now eligible and queued; **851** have a
current-epoch native completion at follow-up. These are exact-ID observations
across non-atomic captures, not a historical or unbounded zero-loss guarantee.
Natural post-fix Workday/SmartRecruiters monitor publication has not yet been
observed. Their selected domains contain 5,232 and 3,411 first-time details ahead
of recurring monitors under the existing strict priority contract. Shared-domain
counts are not per-posting ownership proof. Measure backlog drain and actual
monitor cycles before closing the issue or changing scheduling policy.

## Shared-contract batch and remaining delivery

This release qualifies three full-config scoped normal-Lightpanda detail
replacements and two full-config scoped HTTP monitor readers, retaining original
identities, lanes and config bindings. Six original DOM sources match all fields
through compiled consumers, canonical writes and isolated Linux execution.
Both monitors match fresh original 20-URL inventories over actual HTTP/1.1 and
HTTP/2. Failure and publisher-policy queue cases pass. The immutable detail
membership index removes repeated fleet allocations; its microbenchmark does
not establish production throughput or cost savings.

A roughly **376-second** matched-endpoint live observation records **20 successful
monitors / 323 successful details**, zero claim errors, restarts or OOMs, plus
five monitor failures, eight detail failures and 20 detail host-circuit events.
Twelve crawler actors average **2.36 CPU cores**, with **0.12** for the renderer.
Crawler memory endpoints are 5.02/4.04 GB; renderer endpoints 150/85 MB. This is
uncontrolled live work, excluding remote SQL, Typesense, R2/network and unrelated
host services. It does not prove matched-work efficiency or attributable cost.

At **13:44 UTC**, **1,623,769** owned postings are over 24 hours overdue and
**1,566,722** over 48 hours overdue. Sustained freshness remains unfinished.
Prioritize remaining coverage in shared batches, then native operations, matched
resource/cost evidence, supported final reversal and the recovery window before
retiring production Python, Playwright and Chromium. Preserve useful isolated
offline Python. The [delivery plan](27-go-lightpanda-continuation-plan.md) defines
next actions and exit criteria. The [source1079 aggregate](evidence/go-native1079-serving-checkpoint-2026-10-17.json)
and [previous checkpoint](36-go-lightpanda-document-batch-checkpoint-2026-10-16.md)
retain earlier qualified evidence.
