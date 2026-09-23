# Draft upstream issue: resident memory metric publishes active bytes

Status: prepared locally; not submitted. Checked 2026-09-23. Repository:
[`typesense/typesense`](https://github.com/typesense/typesense).

Suggested title: `typesense_memory_resident_bytes reports stats.active instead of stats.resident`

## Problem

`SystemMetrics::get` reads jemalloc's `stats.resident` into `resident`, but
serializes `active` for `typesense_memory_resident_bytes`. The endpoint therefore
reports the same value for active and resident memory by construction.

Observed at runtime on Typesense 27.1 and 30.2, and confirmed by source inspection in both
[27.1](https://github.com/typesense/typesense/blob/v27.1/src/system_metrics.cpp#L44-L55)
and [30.2](https://github.com/typesense/typesense/blob/v30.2/src/system_metrics.cpp#L41-L52).
For example, the local 30.2 sample benchmark reports `340504576` bytes for both
active and resident memory. The source assignment below establishes the cause.

## Reproduction

Read `GET /metrics.json` on a running 27.1 server. One observed response:

```json
{
  "typesense_memory_active_bytes": "3769458688",
  "typesense_memory_allocated_bytes": "3315307296",
  "typesense_memory_resident_bytes": "3769458688"
}
```

Equality by itself is not proof of the bug; the source assignment identifies
the cause. In `SystemMetrics::get`, `resident` is populated but the response
uses `active`:

```cpp
impl_mallctl("stats.resident", &resident, &sz, nullptr, 0);
// ...
result["typesense_memory_resident_bytes"] = std::to_string(active);
```

## Expected behavior

Publish the successfully retrieved jemalloc `stats.resident` value in the
resident metric, or explicitly document and rename/deprecate this field if
aliasing active bytes is intentional. The apparent assignment fix is to use
`resident` on the right-hand side. The implementation should also handle
unavailable allocator statistics/build configurations safely.

Jemalloc resident bytes and Linux process RSS are different measurements; this
report does **not** require the metric to match `/proc/<pid>/smaps_rollup`.

## Impact

Capacity and before/after optimization reports can incorrectly present active
allocator pages as resident memory. This matters when measuring whether an
index reduction returns memory to the OS. It is a reporting issue, not evidence
of a memory leak or the cause of an OOM.

## Duplicate search

Searched issues and pull requests for `resident`, `resident memory`, and
`typesense_memory_resident_bytes`. Related issues
[#366](https://github.com/typesense/typesense/issues/366) and
[#1595](https://github.com/typesense/typesense/issues/1595) discuss memory metrics
but do not identify this assignment. No dedicated existing report was found.
