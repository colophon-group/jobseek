# B0 whole-lane admission

Issue: [#8648](https://github.com/colophon-group/jobseek/issues/8648).
The ARM64 workflow builds the current crawler slim image, a Chromium-shell
comparator from the same source, and the pinned Lightpanda renderer service.
It runs in disposable Docker networks with no production credentials or writes.

Each c1 arm serves four frozen JSON-LD URLs from one origin. Each c4 arm serves
16 URLs from four isolated HTTP origins, in four waves. Three c1 and five c4
pairs alternate candidate/control order. The candidate seeds literal legacy
schedules with producer routing off, starts the merged Go producer, then transfers them through the
producer's `prepare`/`activate` Unix-socket protocol, and only then starts the
Go supervisor. The comparator uses the current Python browser worker and
Playwright Chromium shell. Both persist through the crawler's ordinary parser
path to disposable PostgreSQL.

The candidate's 1536 MiB limit includes producer (32 MiB), supervisor
(96 MiB), database-only executor (384 MiB), and renderer (1024 MiB). The
comparator has 1536 MiB. Both receive 3.5 CPU units. External cgroup-v2
counters record CPU, synchronized peak and retained memory, and OOM/swap
signals. The evaluator requires exact canonical persisted output, request and
terminal counts, queue conservation, isolated network/cleanup, and a median
correct-URL density ratio above one in both c1 and c4. Every pair's raw
numbers stay in the uploaded report. A close or noisy result calls for another
immutable run; this is no fixed-percentage hurdle or reviewer gate.

The workflow's result is fixture evidence only. Production c1 admission also
needs a read-only receipt/Redis authority preflight, bounded live output and
request checks, and the cold reversal described in the migration plan. Do not
interpret a passing fixture as proof that c1 or c4 traffic is already active.
