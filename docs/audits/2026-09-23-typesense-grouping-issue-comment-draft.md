Adding a standalone, fully synthetic reproduction for the remaining grouping performance issue. I reproduced this on **30.2**, the latest stable release as of 2026-09-23, compared with 27.1. **No `facet_by` is requested**: the slowdown occurs in grouped keyword search itself.

The bounded-memory change is valuable for our deployment, but this query latency increase currently blocks our upgrade. This reproduction isolates the effect of group-key tokenization and type without sharing production data.

### Reproduction

Requires Python 3.10+ (standard library only) and Docker with Linux containers / cgroup v2. The script runs one disposable server at a time, with 4 CPUs and 6 GiB memory/swap combined, bound to loopback. It uses a fixed local test key and removes its containers on success or failure.

- [Standalone script and deterministic data generator](https://github.com/colophon-group/jobseek/blob/fa4235538b35be4a0606f58e1b96e64fd71e3f15/scripts/repro-typesense-grouping.py)
- [Complete measured results, schema, queries, image digests, and result checks](https://github.com/colophon-group/jobseek/blob/fa4235538b35be4a0606f58e1b96e64fd71e3f15/docs/audits/typesense-memory-2026-09-23/grouping-synthetic-repro.json)

```sh
curl -fsSL https://raw.githubusercontent.com/colophon-group/jobseek/fa4235538b35be4a0606f58e1b96e64fd71e3f15/scripts/repro-typesense-grouping.py -o repro-typesense-grouping.py
python3 repro-typesense-grouping.py --output ./grouping-results
```

Use an empty output directory. It retains `documents.jsonl`, `schema.json`, `queries.json`, server logs, per-version results, and `summary.json`. No repository checkout, Python packages, external service, credentials, or separately downloaded dataset are needed.

The generated dataset has **350,000 documents**, 2,000 synthetic company UUIDs with uneven document counts, and selective active/content flags. The keyword query matches exactly **14,000 documents across 800 companies**. All IDs, titles and timestamps are synthetic. The same documents carry three equivalent company keys in one collection:

```json
{
  "token_separators": ["-", "/"],
  "fields": [
    {"name": "company_split", "type": "string", "facet": true},
    {"name": "company_atomic", "type": "string", "facet": true,
     "symbols_to_index": ["-"], "token_separators": ["/"]},
    {"name": "company_numeric", "type": "int32", "facet": true, "sort": false}
  ]
}
```

This is the group-field excerpt; the complete schema is in the script and results. Both string fields store the same UUID. The numeric field is a one-to-one positive integer representation of the company. The atomic field's nonempty separator override keeps each UUID as one token.

Exact grouped query (change only `group_by` to test the other two fields):

```json
{
  "q": "software engineer",
  "query_by": "title",
  "filter_by": "is_active:true && has_content:!=false",
  "sort_by": "_text_match:desc,first_seen_at:desc",
  "group_by": "company_split",
  "group_limit": 10,
  "per_page": 20,
  "include_fields": "id",
  "typo_tokens_threshold": 1,
  "drop_tokens_threshold": 1,
  "use_cache": "false"
}
```

### Results

| Query / group key | 27.1 median / p95 (ms) | 30.2 median / p95 (ms) | Median ratio |
| --- | ---: | ---: | ---: |
| UUID split at hyphens | 39 / 64 | 958 / 1433 | 24.56× |
| Same UUID, whole token | 39 / 44 | 366 / 561 | 9.38× |
| Equivalent positive int32 | 38 / 43 | 88 / 156 | 2.32× |
| Ungrouped count control | 22 / 29 | 19 / 41 | 0.86× |

Timings are server-reported `search_time_ms`; raw server/client timings are retained. Both images run on the same Docker host, sequentially, with identical resource limits. After importing and checking every document, the script restarts the server and checks readiness/document count. Query variants then run in seeded shuffled order, with **3 warm-up rounds and 15 measured rounds**. Query caching is disabled. The ungrouped count control removes grouping and sorting and sets `per_page:0`; it is not a payload-equivalent grouped search.

The script verifies that all variants and both versions return the same matching-document count and the same 20 ordered groups, per-group counts, and ordered document IDs (numeric keys are normalized back to UUIDs). No query cut off, and there were no cgroup memory-limit or OOM events. The approximate grouped `found` value on 30.2 is deliberately excluded from the equality assertion; this report is about latency, not the documented approximation.

Environment: `Linux 6.8.0-64-generic aarch64 GNU/Linux`, official arm64 images, Docker Engine 28.4.0. This is a synthetic arm64 benchmark, not an x86_64 production latency prediction. I have not bisected the first affected release or profiled the exact hot function.

### Potentially relevant implementation detail

The [30.2 grouping path](https://github.com/typesense/typesense/blob/d45d46baf3996d1de8bf96a87f375cfb43691560/src/index.cpp#L2500-L2780) selects candidate groups, builds a filter from their values, and searches again. The strong sensitivity to group-key representation suggests this generated-filter path may be relevant, but that is an inference, not a profiler result. Whole-token UUIDs mitigate the slowdown in this fixture; numeric keys still lag 27.1.

Could this reproduction help identify an optimization that preserves bounded memory? If the no-facet case belongs in a separate issue, I can split it out.
