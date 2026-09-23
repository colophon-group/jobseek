# [30.2] typesense_memory_resident_bytes reports stats.active instead of stats.resident

Status: prepared for review; not submitted upstream.

## Bug Description

On **Typesense 30.2**, `GET /metrics.json` publishes jemalloc's `stats.active` as `typesense_memory_resident_bytes`, even though it separately reads `stats.resident`.

This reproduces with **zero collections and zero documents** on the official image. The runtime symptom is identical active/resident values; the [30.2 source assignment](https://github.com/typesense/typesense/blob/d45d46baf3996d1de8bf96a87f375cfb43691560/src/system_metrics.cpp#L49-L59) establishes why they are identical.

## Reproduction Steps

Requirements: Docker, Bash, curl and jq. No Typesense client library, credentials or dataset are needed.

[Complete standalone Docker/curl reproduction (GitHub blob)](https://github.com/colophon-group/jobseek/blob/ba2a4212efdfe8125e3ae33050de72b76afc9b4e/scripts/repro-typesense-resident-metric.sh) · [captured raw responses and environment](https://github.com/colophon-group/jobseek/blob/ba2a4212efdfe8125e3ae33050de72b76afc9b4e/docs/audits/typesense-memory-2026-09-23/resident-metric-repro-30.2.json)

```bash
curl -fL 'https://raw.githubusercontent.com/colophon-group/jobseek/ba2a4212efdfe8125e3ae33050de72b76afc9b4e/scripts/repro-typesense-resident-metric.sh' \
  -o repro-typesense-resident-metric.sh
bash repro-typesense-resident-metric.sh
```

The script starts a disposable server on a dynamically assigned loopback port, waits for health, verifies `/debug` reports `30.2` and `/collections` is empty, then requests `/metrics.json` three times. It retains the results and removes its own container on exit.

The relevant API call is:

```bash
curl -fsS -H 'X-TYPESENSE-API-KEY: repro-key' "$base/metrics.json" |
  jq '{typesense_memory_active_bytes, typesense_memory_resident_bytes,
       typesense_memory_allocated_bytes, typesense_memory_metadata_bytes}'
```

Here `base` is the disposable server's URL, determined by the script.

## Expected vs Actual

**Expected:** `typesense_memory_resident_bytes` contains the successfully retrieved jemalloc `stats.resident` value.

**Actual:** it contains `stats.active`. Fresh measurements on 30.2:

| Request | active bytes | reported resident bytes | metadata bytes |
| --- | ---: | ---: | ---: |
| 1 | 32,698,368 | 32,698,368 | 10,013,632 |
| 2 | 33,005,568 | 33,005,568 | 10,045,504 |
| 3 | 33,320,960 | 33,320,960 | 10,077,632 |

The first response, projected to the relevant fields:

```json
{
  "typesense_memory_active_bytes": "32698368",
  "typesense_memory_resident_bytes": "32698368",
  "typesense_memory_allocated_bytes": "31400368",
  "typesense_memory_metadata_bytes": "10013632"
}
```

Absolute byte counts can vary by machine and run. Equality alone would not identify the cause; the source reads `stats.resident` into `resident` on line 51, then serializes `active` for the resident field on line 59. The apparent assignment correction is:

```diff
- result["typesense_memory_resident_bytes"] = std::to_string(active);
+ result["typesense_memory_resident_bytes"] = std::to_string(resident);
```

A regression test should verify that the response maps to the resident allocator statistic, with successful statistics retrieval, rather than merely asserting that two live measurements differ.

## Environment

- **Typesense:** 30.2, the [latest stable release](https://github.com/typesense/typesense/releases/tag/v30.2) when checked on 2026-09-23; `/debug` returned `{"state":1,"version":"30.2"}`.
- **Official image:** `typesense/typesense:30.2@sha256:610f2d34b1f93d00762869da2c67736775e5798d19a2c8b91b014b8a0cc1e110`.
- **Server:** Linux `6.8.0-64-generic`, aarch64; Docker Engine 28.4.0, image architecture arm64.
- **Client:** curl 8.4.0 on macOS, jq 1.8.1; no SDK.

## Schema / Configuration

No schema or documents. The official image runs with `--data-dir=/tmp --api-key=repro-key`, otherwise default server configuration. The key is a public local reproduction value.

## Additional Context

The [jemalloc documentation](https://jemalloc.net/jemalloc.3.html#stats.resident) distinguishes allocator resident memory from active allocation pages and from operating-system process RSS. This report asks for the allocator statistic already being read; it does not ask for equality with Linux RSS.

The incorrect mapping can mislead memory-capacity and before/after optimization measurements. It is a reporting defect, not evidence of a leak or the cause of an OOM.

Related discussions: [#366](https://github.com/typesense/typesense/issues/366), [#1595](https://github.com/typesense/typesense/issues/1595). I did not find a dedicated report of this assignment when searching issues and PRs for the metric name and `stats.resident`.
