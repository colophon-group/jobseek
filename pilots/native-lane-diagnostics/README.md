# Bounded canonical comparison diagnosis

This wrapper runs the existing six-arm comparison against disposable fixture
stores and adds a read-only, repeatable-read snapshot after each collected arm.
It reports hashes by profile, table and field, plus whether the snapshot agrees
with the collector's original hash. It never exports identifiers or field values.
The existing canonical parity gate is unchanged.

The dedicated ARM64/generated workflow is diagnostic evidence only. Its measured
window includes these extra queries, so do not substitute its resource numbers
for the standard whole-lane performance comparison. No production endpoint,
credential, queue, configuration, capacity or runtime VERSION is changed.
