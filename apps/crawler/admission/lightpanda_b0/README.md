# B0 whole-lane admission

Issue: [#8648](https://github.com/colophon-group/jobseek/issues/8648).
The ARM64 workflow builds the current crawler slim image, a Chromium-shell
comparator from the same source, and the pinned Lightpanda renderer service.
It runs in disposable Docker networks with no production credentials or writes.

Each c1 arm serves four frozen JSON-LD URLs from one origin. Each c4 arm serves
16 URLs from four isolated TLS origins, in four waves. Three c1 and five c4
pairs alternate candidate/control order. The candidate seeds literal legacy
schedules with producer routing off, starts the merged Go producer, then
transfers them through the producer's `prepare`/`activate` Unix-socket protocol.
Only then does it start the
Go supervisor. The comparator uses the current Python browser worker and
Playwright Chromium shell. Both persist through the crawler's ordinary parser
path to disposable PostgreSQL. The fixture CA is mounted into the renderer's
system trust bundle so its isolated browser child can verify these test origins.

The candidate's 1536 MiB limit includes producer (32 MiB), supervisor
(96 MiB), database-only executor (384 MiB), and renderer (1024 MiB). The
comparator has 1536 MiB. Both receive 3.5 CPU units. External cgroup-v2
counters record CPU, synchronized peak and retained memory, and OOM/swap
signals. Both lanes stay measured for 35 seconds after persistence so the
Go queue's 30-second conservation audit can refresh its gauges. The evaluator
requires exact canonical persisted output, request and
terminal counts, queue conservation, isolated network/cleanup, and a median
correct-URL density ratio above one in both c1 and c4. Every pair's raw
numbers stay in the uploaded report. A close or noisy result calls for another
immutable run; this is no fixed-percentage hurdle or reviewer gate.

The workflow's result is fixture evidence only. Production c1 admission also
needs a read-only receipt/Redis authority preflight, bounded live output and
request checks, and the cold reversal described in the migration plan. Do not
interpret a passing fixture as proof that c1 or c4 traffic is already active.

## First complete ARM64 run

[Run 35857877535](https://github.com/colophon-group/jobseek/actions/runs/35857877535)
completed all 16 arms on 2026-09-23 with exact canonical output, persisted
writes, requests, and queue conservation in every pair. No arm had an OOM,
swap event, restart, or cleanup failure. Median paired results:

| Cohort | Correct URL density, Go / Python | Elapsed, Python / Go | Peak RSS, Go / Python | p99, Go / Python | CPU seconds, Go / Python |
| --- | ---: | ---: | ---: | ---: | ---: |
| c1 (3 pairs) | 3.46× | 2.11× | 0.607× | 0.477× | 24.48 / 13.94 |
| c4 (5 pairs) | 6.82× | 1.75× | 0.258× | 0.571× | 23.99 / 17.86 |

The Go lane used more CPU during the common due and retained sampling window,
despite its lower memory and completion latency. The next run records CPU by
service to localize that cost; production c1 telemetry must confirm it stays
within host capacity. The run's artifact contains every pair and the exact
source/image provenance. This is a fixture pass, not a production canary pass.
