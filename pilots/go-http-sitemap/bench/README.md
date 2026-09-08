# Hermetic concurrent-worker benchmark

This directory is the reproducible local harness for issue #7948. It asks one
narrow question: under one identical process resource envelope, does the warm
Go sitemap executor have materially more saturated throughput than the equivalent warm
Python executor? Results are **saturated executor throughput**, not production throughput,
migration ROI, crawler-wide readiness, infrastructure sizing, or fleet savings.

The capacity corpus is unfiltered. Python therefore calls the unchanged
production `src.core.monitors.sitemap.discover` cached-sitemap path. Literal
filters/transforms are listed as correctness-only exclusions in `corpus.json`;
an admitted future case must call production `monitor_one`, not duplicate its
regex semantics in this harness.

## Frozen shape

- One long-lived process per implementation/level/repetition, alive for client
  construction, worker-pool construction, warmup, and the measured batch.
- Go dispatches with `worker.Pool`; Python uses fixed asyncio worker tasks, a
  bounded input queue, bounded result queue, and bounded unfinished-job slots.
- Both use one process-owned HTTP/1.1 pool, a total/global connection cap of 20,
  a global active-request cap of 20, 10 idle connections globally, and five
  seconds of idle expiry.
- Thirty-two deterministic logical origins are distinct loopback `host:port`
  keys. The job scheduler permits at most two active jobs per origin (one at
  c1). This breadth lets c20/c50 exercise a many-source pool rather than four
  hot origins.
- Python/httpx has no independent per-origin idle setting. Go's per-host idle
  value is an extra safety bound equal to the maximum connections the workload
  can create for that origin, so it cannot bind earlier than the common
  per-origin active limit. The fixture gates historical open connections and
  settled idle connections per origin for both arms. It also reports historical
  server-side idle observations, but does not treat their close-propagation
  window as an internal client-pool bound or claim an httpx setting that does
  not exist.
- The fixed ladder is 1, 5, 20, and 50 service workers plus `overload-c20`.
  Only c5 matches the frozen production monitor semaphore; larger values are
  executor headroom experiments.
- Evidence uses at least 128 warmup jobs, 512 measured jobs per throughput arm,
  and 1,024 jobs in `overload-c20`. The overload arm is only a bounded queue and
  result-conservation stress; it is not open-loop offered-arrival load and does
  not estimate sustainable capacity.
- Root retry recovery and exhaustion are separate policy batches. Their times
  never enter capacity rates because Python jitter and Go waits are different
  current policies.

The default evidence run uses five independent repetitions and alternates arm
order. `--smoke` deliberately uses one repetition and c1/c5 only; its output is
verification, never decision evidence.

## Python production-source seam

Evidence mode stages these exact blobs directly from the issue-frozen
`fb6117b06a3008cdac76adc258327b2f220085f8` Git object before either runner
starts:

- `src/core/monitors/sitemap.py`
- `src/shared/http_retry.py`
- `src/shared/tdm.py`
- `src/shared/constants.py`
- `src/metrics.py`

The frozen commit and all five hashes live in the hashed `corpus.json`; staging
fails if Git cannot reproduce any byte. The frozen identity and the current
`origin/main` commit (context only) are recorded separately in
`source-identity.json`, every raw arm record, and the Python ready record. A
moving branch is never substituted for the frozen comparator. The only
isolation seam replaces the eager all-provider monitor registry's
`register` callback and the unused raw-artifact callback. This avoids importing
unrelated browser/provider/database code while leaving sitemap discovery,
parsing, retry, TDM, and metrics code unchanged. The runner fails if any loaded
production module escapes the staged bundle.

The hashed corpus freezes the exact `uv.lock` SHA-256, Python 3.13.15, every
runtime distribution and version (including HTTP transitives), the expected
absence of `sniffio`, and Debian 13. The Python runner reports the lock hash,
complete installed-distribution census, executable hash, and `/etc/os-release`;
evidence rejects undeclared packages or any mismatch. Builds, dependency sync,
staging, imports, and client/pool startup are outside timing.

The corpus also freezes Go 1.24.0, the module path, and runner package path.
Evidence accepts a runner only when both external and in-process Go build info
match, `vcs.revision` equals the clean repository HEAD, `vcs.modified=false`,
the binary is Linux/amd64 and `-trimpath` was used. The executing runner reports
its own executable hash and build settings, which must match pre-exec inspection.

## Fixture and safety

`fixture.py` is a persistent raw HTTP/1.1 fixture with 32 loopback listeners.
It pre-generates sanitized urlsets and serves one-level indexes, deterministic
retry recovery, and deterministic retry exhaustion. It never reads company
CSVs or other repository data. URLs returned as results use the reserved
`.invalid` TLD and are never fetched.

Runner environments are constructed from a six-key allowlist and contain no
credentials, proxy variables, database URLs, home directory, or repository
paths. A runner receives only its binary/script, the five-file staged Python
source bundle, and an ordered generated job manifest whose digest it verifies.
Targets must resolve exclusively to loopback/private addresses and must match
the declared fixture origin. Every registered job is bound to the exact fixture
server index and exact `Host`; the same path on another of the 32 listeners is
rejected. Evidence reads the orchestrator and runner network namespace, interface
census, IPv4 routes, and IPv6 routes and refuses anything other than a shared,
loopback-only namespace with no usable default or non-loopback route.

Warmup and measured batches have different immutable batch IDs. Fixture retry
attempts are keyed by arm, batch, and job. Tests and runtime checks prove that
warmup attempts cannot change policy-case status order.

## Setup and checks

From `pilots/go-http-sitemap/bench`:

```sh
uv sync --all-groups
uv run ruff check .
uv run python -W error -m unittest -v test_bench.py

cd ..
gofmt -w bench/go-runner/main.go
go test ./...
go test -race ./...
go vet ./...
go build -trimpath -o bench/.bin/go-runner ./bench/go-runner
```

The first local run should be the bounded smoke profile:

```sh
cd bench
uv run python orchestrator.py \
  --smoke \
  --out /tmp/jobseek-7948-smoke \
  --go-binary .bin/go-runner \
  --python .venv/bin/python
```

The smoke run still exercises preload, warmup, alternating order, capacity
jobs, both policy jobs, per-job parity, request transcripts, CPU/RSS sampling,
and shutdown conservation. It is small enough for a disposable CX23 but does
not meet the repetition or ladder evidence gates. A 3.13 patch other than
3.13.15 is permitted only in this explicitly labelled smoke mode and is
reported in every arm's ready record.

## Evidence execution

The orchestrator deliberately refuses evidence unless all of these are true:

- Linux/amd64;
- at least five repetitions and the complete declared ladder;
- the runner is alone in one stable cgroup-v2 path with exactly one vCPU,
  exactly 1 GiB combined memory+swap, and exactly 128 PIDs;
- the fixture/orchestrator is outside that measured cgroup;
- the same cgroup path and exact limits hold before and after every arm, the
  fingerprint is identical across all arms, and the cgroup is empty after each
  runner exits;
- the runner soft file-descriptor limit is exactly 256;
- the whole container has Docker `NetworkMode=none` and runtime inspection also
  proves that only loopback interfaces/routes exist;
- base, image, and container inspect JSON report the exact frozen base-layer
  prefix, supplied derived image ID, read-only root, fixed entrypoint, three
  exact mounts, and `NetworkMode=none`;
- `/etc/hostname` and `/proc/self/cgroup` bind the executing orchestrator to the
  inspected container ID, so a detached operator-created JSON file is rejected;
- the fixed Python path and executable hash equal the immutable attestation
  generated during the image build, and the exact package/OS/lock census stays
  identical across arms;
- exact Go 1.24.0 deterministically rebuilds the committed in-image runner at
  evidence startup, and its bytes, hash, and build information equal the staged
  binary that is subsequently executed.

Evidence has no operator-supplied executable or runner-prefix option. It uses
the fixed in-image Python, Go binary, source tree, and checked-in cgroup wrapper;
only the dedicated cgroup path varies. The wrapper validates exact controls,
refuses a non-empty cgroup, moves itself, and `exec`s the runner.

### Reproducible evidence runbook

Run this only on a disposable, non-production Linux/amd64 host with no
production services, credentials, repository checkout, or sensitive mounts.
Do not run it from a developer or root checkout. The context below is a new
unauthenticated public sparse clone. A deny-all Dockerfile-specific ignore file
admits only the benchmark, pilot Go packages, five frozen Python source paths,
the crawler Dockerfile, and public Git metadata. It excludes every `.env*`,
company-data directory, credential, and unrelated repository path.

Set `EVIDENCE_COMMIT` to the full 40-hex commit at the public PR head. This
allows evidence to run before merge; a branch name, abbreviated SHA, moving
`origin/main`, or a commit unavailable from the public remote is rejected.

```sh
set -eu
: "${EVIDENCE_COMMIT:?set the full public PR-head commit SHA}"
printf '%s\n' "$EVIDENCE_COMMIT" | grep -Eq '^[0-9a-f]{40}$'
RUN_ROOT=$(mktemp -d /private/tmp/jobseek-7948-public.XXXXXXXX)
SOURCE="$RUN_ROOT/source"
EVIDENCE_DIR="$RUN_ROOT/evidence"
mkdir "$EVIDENCE_DIR"

git clone --filter=blob:none --no-checkout \
  https://github.com/colophon-group/jobseek.git "$SOURCE"
git -C "$SOURCE" sparse-checkout init --no-cone
git -C "$SOURCE" sparse-checkout set \
  /apps/crawler/Dockerfile \
  /apps/crawler/src/core/monitors/sitemap.py \
  /apps/crawler/src/shared/http_retry.py \
  /apps/crawler/src/shared/tdm.py \
  /apps/crawler/src/shared/constants.py \
  /apps/crawler/src/metrics.py \
  /pilots/go-http-sitemap/go.mod \
  /pilots/go-http-sitemap/go.sum \
  /pilots/go-http-sitemap/boundedhttp/ \
  /pilots/go-http-sitemap/sitemap/ \
  /pilots/go-http-sitemap/worker/ \
  /pilots/go-http-sitemap/bench/
git -C "$SOURCE" fetch origin "$EVIDENCE_COMMIT"
git -C "$SOURCE" checkout --detach "$EVIDENCE_COMMIT"
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$EVIDENCE_COMMIT"
git -C "$SOURCE" fetch origin fb6117b06a3008cdac76adc258327b2f220085f8
for path in \
  apps/crawler/Dockerfile \
  apps/crawler/src/core/monitors/sitemap.py \
  apps/crawler/src/shared/http_retry.py \
  apps/crawler/src/shared/tdm.py \
  apps/crawler/src/shared/constants.py \
  apps/crawler/src/metrics.py; do
  git -C "$SOURCE" show "fb6117b06a3008cdac76adc258327b2f220085f8:$path" >/dev/null
done
test "$(git -C "$SOURCE" remote get-url origin)" = \
  https://github.com/colophon-group/jobseek.git
test -z "$(git -C "$SOURCE" status --porcelain --untracked-files=all)"
test -z "$(git -C "$SOURCE" ls-files --others --ignored --exclude-standard)"

BASE='python:3.13.15-slim-trixie@sha256:7ce4b6dfe35e55397b7cda544f8a13f191b7ae28dc5aad71fe664dbc9bc2623f'
docker pull --platform linux/amd64 "$BASE"
docker image inspect "$BASE" > "$EVIDENCE_DIR/base-inspect.json"
docker build --platform linux/amd64 \
  --build-arg BUILDKIT_CONTEXT_KEEP_GIT_DIR=1 \
  --build-arg "EVIDENCE_COMMIT=$EVIDENCE_COMMIT" \
  -f "$SOURCE/pilots/go-http-sitemap/bench/evidence/Dockerfile" \
  -t jobseek-7948-evidence:local "$SOURCE"
DERIVED_IMAGE_ID=$(docker image inspect --format '{{.Id}}' jobseek-7948-evidence:local)
docker image inspect jobseek-7948-evidence:local > "$EVIDENCE_DIR/image-inspect.json"

CGROUP_NAME="jobseek-7948-runner-$$"
CGROUP="/sys/fs/cgroup/$CGROUP_NAME"
CONTAINER_ID=
cleanup() {
  set +e
  if test -n "$CONTAINER_ID"; then
    docker kill "$CONTAINER_ID" >/dev/null 2>&1
    docker rm -f "$CONTAINER_ID" >/dev/null 2>&1
  fi
  if test -f "$CGROUP/cgroup.kill"; then
    printf '1\n' | sudo tee "$CGROUP/cgroup.kill" >/dev/null
  elif test -f "$CGROUP/cgroup.procs"; then
    for pid in $(cat "$CGROUP/cgroup.procs"); do
      sudo kill -KILL "$pid" >/dev/null 2>&1
    done
  fi
  attempts=0
  while test -f "$CGROUP/cgroup.procs" && \
        test -n "$(cat "$CGROUP/cgroup.procs")" && test "$attempts" -lt 100; do
    attempts=$((attempts + 1))
    sleep 0.05
  done
  sudo rmdir "$CGROUP" >/dev/null 2>&1 || \
    printf 'warning: cgroup cleanup requires operator attention: %s\n' "$CGROUP" >&2
}
trap cleanup EXIT INT TERM HUP

sudo mkdir "$CGROUP"
printf '100000 100000\n' | sudo tee "$CGROUP/cpu.max" >/dev/null
printf '1073741824\n' | sudo tee "$CGROUP/memory.max" >/dev/null
printf '0\n' | sudo tee "$CGROUP/memory.swap.max" >/dev/null
printf '128\n' | sudo tee "$CGROUP/pids.max" >/dev/null

CONTAINER_ID=$(docker create --platform linux/amd64 --network none \
  --cgroupns host --privileged --read-only \
  --tmpfs /tmp:rw,nosuid,nodev \
  --mount "type=bind,src=$SOURCE,dst=/source-proof,readonly" \
  --mount "type=bind,src=$EVIDENCE_DIR,dst=/evidence" \
  --mount type=bind,src=/sys/fs/cgroup,dst=/sys/fs/cgroup \
  jobseek-7948-evidence:local \
    --evidence \
    --evidence-commit "$EVIDENCE_COMMIT" \
    --derived-image-id "$DERIVED_IMAGE_ID" \
    --docker-base-inspect /evidence/base-inspect.json \
    --docker-image-inspect /evidence/image-inspect.json \
    --docker-container-inspect /evidence/container-inspect.json \
    --runner-cgroup "$CGROUP" \
    --out /evidence/attempt-001)
docker inspect "$CONTAINER_ID" > "$EVIDENCE_DIR/container-inspect.json"
docker start --attach "$CONTAINER_ID"

test -z "$(cat "$CGROUP/cgroup.procs")"
```

Retain the three inspect JSON files with the attempt and then destroy the
disposable host. `docker create --network
none` is not trusted by prose alone: container inspection and in-process
namespace inspection are both gates. The fixture remains outside the measured
child cgroup but in the same no-network namespace, so all 32 listeners are
reachable only by loopback.

Docker's reported base `RootFS.Layers` must be the exact prefix of the derived
image's layers. This is a frozen Docker metadata chain, not an independent
cryptographic proof of registry ancestry. Live container, executable, source,
runtime, OS, lock, and package attestations provide the remaining bindings.

## Fail-closed evidence and outputs

The orchestrator stops on any manifest, URL digest/count, typed outcome,
explicit request, wire attempt, status sequence, response-byte, protocol,
origin, concurrency, queue, connection, result-conservation, process-child, or
config mismatch. Batch output has an external deadline; a dead worker task or
lost result causes process-group termination rather than an incomplete
observation. The fixed wrapper must `exec`, and invalid or unexpected JSON
startup records also tear down the complete process group.

An output directory is created once and contains:

- `manifests/`: exact ordered warmup/measured job inputs and digests;
- `raw/fixture.jsonl`: authoritative connection/request/status/byte transcript;
- `raw/arms.jsonl` and `raw/jobs.jsonl`: resource and per-job observations;
- `raw/stderr/`: production retry logs, kept outside the JSON protocol;
- `preexec/`: exact corpus, corpus checksum, runner script, and Go binary used;
- `harness-identity.json`, `source-identity.json`, `image-identity.json`,
  `commands.json`, and `environment.json`;
- evidence-only base/image/container inspect JSON, build-produced Python
  runtime attestation, deterministic Go rebuild comparison, and public-source
  manifest comparison;
- `summary.json`, `report.md`, and `attempt-status.json`.

Per-arm observations include completed/successful jobs per second; queue,
service, and end-to-end p50/p95/p99; CPU seconds per successful job; steady and
sampled peak process-tree RSS; cumulative runner peak RSS; historical maximum
queued, in-flight, accepted-but-unfinished, and per-origin service work; file
descriptors; historical open and reported server-side idle connections;
requests; response bytes; root attempts and root retry intervals; timeouts;
panics; crashes; OOMs; and terminal unfinished jobs. Index children are planned
requests and are never counted as retries. Runners
are also checked to have no child processes, making their self CPU delta equal
to process-tree CPU for this harness.

The summary reports paired saturated Go/Python successful-cycle ratios and a
deterministic bootstrap lower 95% bound. The 85% derived rate is planning
headroom only and is never a gate. Only c5 is decision-bearing: in evidence
mode it requires at least five paired repetitions, a lower bound of at least
1.20x, zero errors and parity in every pair, and no p99 or steady-RSS regression
in any individual pair. Aggregate medians cannot hide one regressing pair. All other levels are
diagnostics, `overload-c20` is conservation stress, and smoke mode can never
set a decision gate true. Even a passing c5 result permits only continued
bounded migration work under `WORKER-BENCHMARK.md`.
