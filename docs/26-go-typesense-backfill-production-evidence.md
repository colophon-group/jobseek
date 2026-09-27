# Go Typesense backfill production proof — 2026-09-27

[Maintenance run 36317322087](https://github.com/colophon-group/jobseek/actions/runs/36317322087)
completed successfully at 14:25 UTC against deployed crawler
`fcb19acf39ab8f55687fa870de4e2e15dd21beb0` (v0.13.864). The whole chain held
the crawler mutation lock. Its stages were Go backfill, Python full fresh
reconciliation, then Python taxonomy verification. The latter two move to Go
in candidate #10089; this proof does not claim they were already Go.

## Output and parity

- Go backfill acknowledged **5,669,012 documents** and completed at
  13:27:38.969 UTC after 5,472.035 seconds.
- Full reconciliation run `0fb6b8bc-866f-4ed7-892d-977d07dd3eb1` completed all
  **256 partitions** at 14:25:15.912 UTC: 5,680,699 local and 5,680,697 remote
  rows checked, 20 differences detected and repaired, **zero unresolved**.
  The source remained live during this cycle; these totals are not a single
  frozen count or an assertion that no changes happened after backfill.
- Taxonomy verification returned `ready` at 14:25:41 UTC. Exact static
  projections matched for all 37,526 location, 562 occupation, 36 seniority,
  186 technology, and 6,034 company documents. All six active collection
  schemas passed. Dynamic count fields were excluded by the existing verifier.
- The scheduled count refresh queued behind this proof also completed
  successfully in [run 36324375340](https://github.com/colophon-group/jobseek/actions/runs/36324375340).

The local mode-0600 log is `/tmp/jobseek-backfill-proof-36317322087.log`.
GitHub's run log is the durable command/result record.

## Resource observations and limits

The observer retained 346 successful samples during the Go phase, from
11:58:41.596 through 13:27:37.094 UTC, at about 15-second intervals. The final
sample reported cumulative container CPU 784,207,316 microseconds; maximum
sampled cgroup `memory.current` was 100,868,096 bytes. Final Go RSS was
79,636 KiB and process high-water RSS 108,976 KiB. Samples include container
supervision/observation overhead. Startup samples were incomplete, and the
host did not expose cgroup `memory.peak`.

Raw observations remain mode 0600 at
`/tmp/jobseek-go-backfill-fcb19acf-resources.jsonl`. Later observations cover
Python reconciliation and must not be attributed to Go backfill. These are
maintenance measurements, with no same-workload Python baseline or operating
cost result; they do not satisfy the whole-crawler resource gate in #7966.

## Release state

Candidate #10089 passed Required CI, installed-image parity, and Crawler
Deploy Gate. The supported c1 rollback and release sequence follows the
finished proof. Its deployment and natural Go maintenance output require
separate evidence; the complete migration goal remains open.
