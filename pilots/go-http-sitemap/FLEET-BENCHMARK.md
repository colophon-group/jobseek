# Go/Python sitemap fleet benchmark

Issue #8648 is a bounded, read-only comparison of the Go candidate and the
current Python sitemap monitor. It asks whether Go's smaller runtime overhead
creates useful concurrency headroom under the crawler's RAM ceiling. It does
not change the authoritative worker, queue, database, exporter, or publisher.

## Frozen comparison

- 32 distinct production origins whose configured root currently returns one
  direct XML `urlset`; sitemap indexes are rejected before a child request.
- The manifest is embedded into both images. A test proves that every board
  URL, sitemap URL, and literal `url_filter` still exactly matches
  `apps/crawler/data/boards.csv`.
- Scheduled profiles `c5`, `c12`, and `c16` vary only active worker permits. Both
  runtimes retain a fixed HTTP/1.1 pool of 20 total and 10 idle connections
  with 30-second keepalive expiry.
- `c5` is the current Python operating-point anchor. `c12` and `c16` measure
  bounded density/headroom only; 32 jobs are not enough to establish sustained
  production capacity at those levels.
- Every fresh container runs a connection-cold batch followed by a
  `pool_warm` batch through the same long-lived worker and client. `pool_warm`
  means the pool survives between batches; it does not assert that all 32
  origins retained an idle connection.
- The committed seed-8648 admission schedule contains 18 independent
  Go/Python pairs: six pairs each for `c5`, `c12`, and `c16`. Each consecutive
  three-pair block contains all three profiles, every profile occupies each
  within-block position twice, and Go-first/Python-first order is balanced per
  profile across early, middle, and late blocks. The two rounds inside a
  container are correlated observations, not independent samples.
- This admission-only schedule replaces the original 30-pair exploratory
  schedule after run 34376977564 completed its first 50 arms, then encountered
  cross-runtime timeouts, slow responses, and status rejections from three
  external origins in seven of the final ten arms. The descriptive `c2`, `c4`,
  and `c8` pairs controlled no admission threshold, so retaining them would add
  48 GETs per origin without answering the RAM-density decision. Results from
  that failed run are not pooled into the replacement run.
- Each arm is limited to 1 CPU, 1 GiB total memory with no additional swap,
  128 PIDs, and 256 file descriptors. Containers are non-root, read-only,
  capability-free, and run one at a time on the allocated Murmur machine. The
  local log driver keeps one uncompressed 256 KiB file per ephemeral arm.
- Before and after image pulls, Murmur must provide at least 1.5 GiB available
  memory, 5 GiB Docker storage, and a one-minute load average no greater than
  1.50. At each gate, the workflow waits up to 60 seconds for transient load to
  fall below that unchanged ceiling and rechecks protected services before
  continuing.
- Every job permits exactly one GET, no retry, redirect, proxy, or sitemap-index
  expansion. Requests use `Accept-Encoding: identity`; decoded response and
  aggregate byte caps remain enforced.

The full schedule makes at most 2,304 source GETs: 18 pairs x 2 runtimes x 2
rounds x 32 origins, or 72 GETs per origin. DNS is resolved once before the
schedule, restricted to public addresses that are not Murmur, and the exact
same singleton host pins are injected into both runtimes. There is no
benchmark-only in-container DNS fanout before the measured rounds: Python
validates each request and Go validates each dial against those pinned public
answers on their normal transport paths. No crawler secrets, volumes, Docker
socket, Redis, Postgres, R2, Typesense, or Lightpanda endpoint are available to
either container.

## Admission and interpretation

Correctness is the first gate. Every Go/Python job pair in every round must
produce the same non-empty canonical URL count and length-prefixed SHA-256
digest. Failed, timed-out, or OOM-killed arms remain failed observations; they
are never silently replaced or removed from the artifact.

Only after parity should the comparison use paired results for jobs/second,
CPU/job, process and cgroup memory, file descriptors, and `c16` versus `c5`
scaling. Per-pair, per-round decoded-byte deltas are always disclosed. Timing
is comparable only when every aggregate Go/Python byte delta is at most 5%; a
larger difference leaves the artifact valid but makes the performance result
`inconclusive_workload_imbalance`. It does not invalidate count/hash parity or
trigger more source requests. `ru_maxrss` and cgroup `memory.peak` are lifetime
high-water values, not phase-specific incremental memory. If neither runtime
approaches 1 GiB, the result demonstrates headroom for this slice, not the
production RAM ceiling. Per-arm p99 across 32 jobs is effectively a maximum
and is descriptive only.

The Go density case is admitted only when all 36 arms succeed, every paired
round has identical non-empty count and digest, no policy, OOM, container, or
resource invariant fails, and all aggregate byte deltas remain within 5%.
Collapse each arm's cold and warm rounds deterministically: throughput is
`64,000 / sum(round run_duration_ms)` jobs/second, CPU/job is
`sum(round cpu_user_ms + cpu_system_ms) / 64`, and density is arm throughput
divided by arm `cgroup_memory_peak_bytes`. For each schedule pair, divide the
Go arm metric by its Python arm metric; each thresholded profile result is the
median of its six paired ratios. At both `c12` and `c16`, median paired density
must be at least 1.25, at least five of six density ratios must be greater than
1.0, and the median of the six paired Go/Python cgroup-peak ratios must be no
more than 0.85. Median paired throughput must be at least 0.90 at `c5`, `c12`,
and `c16`; median paired CPU/job must be no more than 1.25 at `c12` and `c16`.
Finally, the median of the six Go arm throughputs at `c16` must be at least 1.5
times the median of the six Go arm throughputs at `c5`. A valid parity result
that misses any threshold remains useful correctness evidence but does not
establish a RAM-density advantage or authorize a resident shadow.

A repeatable Go density advantage authorizes continued work on a long-lived,
continuously fed Go worker with bounded global and per-origin permits. It does
not authorize production concurrency 16 or Go ownership of queue lifecycle,
database writes, scrape processing, browser work, or publishing. Those mixed
production costs need a later shadow slice and a separate rollback gate.

## Reproduction and evidence

The manual `crawler-sitemap-fleet-benchmark.yml` workflow is dispatchable only
from merged `main`. It builds immutable ARM64 images for both runtimes, pins
them by digest, verifies protected Murmur services before and after every arm,
and uploads one sanitized JSON artifact even when a benchmark arm fails.
Each container's inherited image entrypoint and explicit profile command are
attested separately so the Go and Python image layouts receive the same check.
Cleanup discovers exact-owned containers by their full 64-character IDs before
removal; it does not act on unrelated containers.
Production-safety invariant failures stop the schedule immediately.
If preflight fails before `RUN_META`, the remote cleanup trap emits exactly one
record containing an allowlisted stage name, the exit status, and the protected-
service baseline digest (or `none` if no baseline was established). Such a
failure is classified as ordinary benchmark infrastructure only when the
independent postflight digest exactly matches that baseline; missing or changed
protected-state evidence remains a production-safety abort. Raw remote stderr,
host addresses, credentials, and temporary paths are never retained.

Run local conformance from `pilots/go-http-sitemap`:

```sh
gofmt -w boundedhttp/*.go sitemap/*.go benchmark/*.go cmd/fleetbench/*.go
go test ./...
go test -race ./...
go vet ./...
python3 -m unittest discover -s python_shadow -p 'test_*.py'
python3 -m unittest discover -s benchmark -p 'test_report_wire.py'
```
