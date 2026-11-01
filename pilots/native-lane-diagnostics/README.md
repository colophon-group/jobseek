# Bounded canonical comparison diagnosis

This wrapper runs the existing six-arm comparison against disposable fixture
stores and adds a read-only, repeatable-read snapshot after each collected arm.
It reports hashes by profile, table and field, plus whether the snapshot agrees
with the collector's original hash. It never exports identifiers or field values.
The existing canonical parity gate is unchanged.

The publication probe tests whether a single-row or complete-profile upload
pointer/enrichment-flag seam reproduces the original failed aggregate hash.
Only matching profile/kind/count metadata is reported; values remain in memory.
It also exhausts publication subsets within fixture profiles of at most 12 jobs.
It never searches subsets of the 500-job rich profile or changes stored rows.

The dedicated ARM64/generated workflow is diagnostic evidence only. Its measured
window includes these extra queries, so do not substitute its resource numbers
for the standard whole-lane performance comparison. No production endpoint,
credential, queue, configuration, capacity or runtime VERSION is changed.
