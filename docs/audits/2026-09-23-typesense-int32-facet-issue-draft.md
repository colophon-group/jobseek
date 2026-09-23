# [30.2] int32 facet values -1 and 1 collide: filtered hits and facet labels disagree

Status: prepared for review; not submitted upstream.

## Bug Description

On **Typesense 30.2**, an `int32` facet can label a matching value `1` as `-1`, and merge the two values into one bucket. A minimal reproduction uses one field and **two sequential document inserts**: `-1` first, then `1`.

With `filter_by=experience_min:>=0`, the returned hit correctly contains `experience_min: 1`, but the facet says `{"value":"-1","count":1}`. This occurs with both `automatic` and `exhaustive` facet strategies. Sampling is false and the search is not cut off.

**Possibly related to [#2720](https://github.com/typesense/typesense/issues/2720)**, which reports incorrect numeric facet statistics involving `-1`. This reproduction additionally demonstrates incorrect value labels and merged buckets on the latest stable release; it may share the same underlying cause.

## Reproduction Steps

Requirements: Docker, Bash, curl and jq. All data is synthetic.

[Complete standalone Docker/curl reproduction (GitHub blob)](https://github.com/colophon-group/jobseek/blob/ba2a4212efdfe8125e3ae33050de72b76afc9b4e/scripts/repro-typesense-int32-facet.sh) · [raw responses for all comparison cases](https://github.com/colophon-group/jobseek/blob/ba2a4212efdfe8125e3ae33050de72b76afc9b4e/docs/audits/typesense-memory-2026-09-23/int32-facet-repro-30.2.json)

```bash
curl -fL 'https://raw.githubusercontent.com/colophon-group/jobseek/ba2a4212efdfe8125e3ae33050de72b76afc9b4e/scripts/repro-typesense-int32-facet.sh' \
  -o repro-typesense-int32-facet.sh
bash repro-typesense-int32-facet.sh
```

The script starts a disposable official 30.2 server on a loopback port, verifies its version, executes the API steps below plus comparison cases, checks the reported behavior, and cleans up its own container. It prints the directory containing all responses.

These are the essential curl steps against that empty server (`base` is its URL):

```bash
api() { curl -fsS -H 'X-TYPESENSE-API-KEY: repro-key' "$@"; }

api -X POST -H 'Content-Type: application/json' "$base/collections" -d '{
  "name": "repro",
  "fields": [
    {"name": "experience_min", "type": "int32", "facet": true, "sort": false}
  ]
}'

# Keep these as separate requests, in this order.
api -X POST -H 'Content-Type: application/json' \
  "$base/collections/repro/documents" \
  -d '{"id":"negative","experience_min":-1}'

api -X POST -H 'Content-Type: application/json' \
  "$base/collections/repro/documents" \
  -d '{"id":"positive","experience_min":1}'

api --get "$base/collections/repro/documents/search" \
  --data-urlencode 'q=*' \
  --data-urlencode 'filter_by=experience_min:>=0' \
  --data-urlencode 'facet_by=experience_min' \
  --data-urlencode 'facet_strategy=exhaustive' \
  --data-urlencode 'per_page=10' \
  --data-urlencode 'max_facet_values=10'
```

## Expected vs Actual

**Expected:** one hit containing `1` and one facet bucket `{"value":"1","count":1}`.

**Actual** (response excerpt):

```json
{
  "found": 1,
  "hits": [
    {"document": {"experience_min": 1, "id": "positive"}}
  ],
  "facet_counts": [{
    "field_name": "experience_min",
    "counts": [{"count": 1, "highlighted": "-1", "value": "-1"}],
    "sampled": false,
    "stats": {"avg": 1.0, "max": 1.0, "min": 1.0, "sum": 1.0, "total_values": 1}
  }],
  "search_cutoff": false
}
```

Changing the filter to `experience_min:>=-1` returns both correct documents, but `exhaustive` merges their facets into `{"value":"-1","count":2}`. Expected: distinct `-1` and `1` buckets, each with count 1.

Fresh comparison results using the same two sequential inserts and the non-negative filter:

| Field configuration | automatic | exhaustive | top_values |
| --- | --- | --- | --- |
| int32, sort:false | wrong label -1 | wrong label -1 | correct label 1 |
| int32, sort:true | wrong label -1 | wrong label -1 | correct label 1 |
| int64, sort:false | correct label 1 | correct label 1 | correct label 1 |

Thus enabling the sort index does not fix this minimal case. `top_values` and `int64` are successful controls here, not claims of general workarounds for every dataset.

## Environment

- **Typesense:** 30.2, the [latest stable release](https://github.com/typesense/typesense/releases/tag/v30.2) when checked on 2026-09-23; verified with `/debug`.
- **Official image:** `typesense/typesense:30.2@sha256:610f2d34b1f93d00762869da2c67736775e5798d19a2c8b91b014b8a0cc1e110`.
- **Server:** Linux `6.8.0-64-generic`, aarch64; Docker Engine 28.4.0, image architecture arm64.
- **Client:** curl 8.4.0 on macOS, jq 1.8.1; no SDK.

## Schema / Configuration

The complete schema and both documents are above. Default server configuration apart from `--data-dir=/tmp --api-key=repro-key`. No joins, nested fields, grouping, updates, bulk import, custom sorting or facet sampling.

## Additional Context

Source inspection suggests a facet-ID collision:

- [Indexing int32 values](https://github.com/typesense/typesense/blob/d45d46baf3996d1de8bf96a87f375cfb43691560/src/index.cpp#L813-L819) reinterprets their bits as `uint32_t`; `-1` becomes `UINT32_MAX`.
- [Facet insertion](https://github.com/typesense/typesense/blob/d45d46baf3996d1de8bf96a87f375cfb43691560/src/facet_index.cpp#L33-L45) treats `UINT32_MAX` as the marker for allocating a new facet ID. The [counter starts at zero](https://github.com/typesense/typesense/blob/d45d46baf3996d1de8bf96a87f375cfb43691560/include/facet_index.h#L123), so inserting `-1` first can assign ID `1`, which also represents literal int32 value `1`.
- [The reverse map](https://github.com/typesense/typesense/blob/d45d46baf3996d1de8bf96a87f375cfb43691560/src/facet_index.cpp#L57-L68) uses that ID to identify the displayed value.

This explains the observed label collision, but I have not built or validated a source patch. A fix should preserve distinct identities for every supported int32 value. A regression test could exercise the two insertion orders, filtered labels, distinct bucket counts, and the automatic/exhaustive/top_values paths.

The user-visible impact is an incorrect filter option even when the hit and total match count are correct. This is distinct from tolerating approximate counts.
