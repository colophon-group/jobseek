# Go + Lightpanda lifecycle pilot

This is a deliberately small, nonproduction experiment for driving Lightpanda
0.4.0 through CDP with `chromedp` 0.14.2. One invocation accepts one absolute
HTTP(S) URL and one synchronous JavaScript expression. It returns bounded JSON
containing the top-level document's final response status, final URL,
`<html>` outerHTML, and the expression value.

The same package also contains a dormant in-process implementation of the
runtime-v1 `lightpandaadapter.Runner`. It maps only B0 render or B1 render plus
one synchronous evaluation declared to have no network effect onto exactly
one existing one-shot lifecycle. It is exposed only through the dormant
render-only framed mode below and adds no service, endpoint, queue,
persistence, fallback, deployment, or production routing. The B1 constructor
still requires an injected evaluation-privacy implementation; only a
loopback-fixture sealer exists in tests.

An explicit `--runtime-v1-stdio` mode exposes the narrower render-only adapter
for offline fixtures and CI. Its process arguments contain only that fixed
flag. It reads exactly one canonical, prefix-inclusive bounded varint frame
containing `BrowserExecutionInput`, requires EOF, and writes exactly one
deterministically encoded `BrowserResult` frame to stdout under a 2 MiB
prefix-inclusive cap. Stdout is wire-only; malformed, oversized, trailing, and
invalid inputs produce typed, message-bounded results when output remains
writable. The mode does not admit evaluation plans, so it needs no placeholder
privacy implementation and rejects `EVALUATE` before the runner or origin can
be contacted. SIGINT or SIGTERM requests best-effort asynchronous closure of
only the process-owned stdin handle. Input parsing runs in an isolated
goroutine so cancellation can write its one result and let the one-shot
process exit even on Darwin, where closing a pipe-backed stdin can itself wait
behind the blocked read. Reusable borrowed callers keep ownership of their
readers and are never closed implicitly; borrowed inputs must be nonblocking
because cancellation cannot interrupt an active borrowed read. A reusable
caller that supplies a blocking owned input must provide an interrupt that
returns promptly if it needs goroutine cleanup without process exit.

The package also contains a non-authoritative bounded execution pool for the
fixed-RAM pilot. The pool admits immutable runtime-v1 inputs into a fixed set
of workers, dispatches different origins fairly, and permits at most one task
per origin at a time. Every active slot still owns a separate one-shot
Lightpanda process and the same cleanup proof below. Processes are not reused
or shared between tasks. The ordinary build has no pool CLI, queue, crawler, or
production entry point. A separate `densitybench` build tag exposes only the
closed-input implementation smoke described below.

The bridge preserves the pilot's stricter 512 KiB HTML ceiling and reports an
oversized document as the adapter's closed `RESOURCE_LIMIT` result. B1 uses
the smaller of the plan's `max_result_bytes` and the pilot's 64 KiB ceiling.
A typed cleanup-verification failure remains authoritative over a concurrent
timeout or cancellation; other provider details are reduced to message-free,
fail-closed adapter errors.

The runner starts a new Lightpanda process for its single task on a chosen free
loopback port. The child receives a fixed non-secret environment that disables
Lightpanda telemetry and core dumps; it inherits no parent credentials or
configuration through environment variables. Concurrent in-process runners retain
each allocated port until that process has been cleaned up, preventing sibling tasks
from selecting the same close-then-bind port:

```text
lightpanda serve --host 127.0.0.1 --port <port> --log-level error
```

It polls `http://127.0.0.1:<port>/json/version`, accepts only a `ws` endpoint
whose literal host is `127.0.0.1` and whose port is the allocated port, checks
that the child is still running before and after readiness, and creates a fresh
remote allocator and target. After every outcome, including timeout, it
terminates the Lightpanda process group, waits/reaps the leader, and verifies
that both the process group and listener are gone. A cleanup verification
failure makes that one-shot invocation fail. Linux children also request
`SIGKILL` from the kernel when their Go parent dies; supported non-Linux Unix
builds retain the separate-process-group behavior.

## Build and run

The image supports Linux amd64 and arm64. Its Dockerfile selects the official
Lightpanda 0.4.0 binary by Docker target architecture and verifies the release
SHA-256 before installing it:

- amd64 `lightpanda-x86_64-linux`:
  `bfcf9bd7e80939b87232aa114a49d8f397f51af0c2632d9fc58d4a6d4386624f`
- arm64 `lightpanda-aarch64-linux`:
  `5e3b54deed642ffeb2b8f24a1931e54c51161f44d9d728135da3d4863cb722fb`

The runtime stage uses the digest-pinned multi-architecture Debian Trixie
2026-08-24 slim image. Its glibc 2.41 satisfies the arm64 release binary's
GLIBC 2.38 requirement. The Go builder tag and packages resolved during
`apt-get` are not snapshot-pinned, so the complete build is not reproducible.

```sh
docker build \
  --build-context contracts=../../apps/crawler/contracts \
  --platform linux/amd64 \
  --target runtime \
  -t jobseek-lightpanda-pilot .

docker run --rm \
  --cpus=1 \
  --memory=512m \
  --memory-swap=512m \
  --pids-limit=128 \
  --network=bridge \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
  jobseek-lightpanda-pilot \
  'https://staging.example.test/jobs' \
  'document.title'
```

The framed render-only handler uses stdin/stdout rather than target or
expression arguments. This example is for an already isolated offline fixture
only; `request.frame` must contain one runtime-v1 input record followed by EOF:

```sh
docker run --rm -i \
  --cpus=1 --memory=512m --memory-swap=512m --pids-limit=128 \
  --network=none --cap-drop=ALL --security-opt=no-new-privileges \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
  jobseek-lightpanda-pilot --runtime-v1-stdio \
  <request.frame >result.frame
```

Those CPU, memory, PID, network-namespace, capability, privilege,
read-only-root, and tmpfs controls are caller responsibilities and are required
for every pilot run. The public CLI still runs exactly one task per container;
the test-only fixed-RAM pool may run its declared number of independent
one-shot processes inside one harder whole-container bound. Keep Docker's
private PID namespace and a dedicated network namespace: never use
`--pid=host`, `--network=host`, or expose an internal CDP port. The unavoidable
close-then-bind free-port handoff and numeric process-group cleanup are accepted
only under those isolation conditions.

Do not point this pilot at a production origin; use a disposable fixture, local
test server, or explicitly authorized staging origin. Ordinary
destination-network controls remain a caller concern.

Unit tests run without Lightpanda:

```sh
go test ./...
```

The real-binary integration test is opt-in on Linux amd64 and arm64 and
verifies the architecture-specific stable-0.4.0 checksum before use. It serves
the navigated page from a local `httptest` origin, so test execution requires
no destination network. The direct command is only for an already-isolated
disposable Linux environment:

```sh
LIGHTPANDA_INTEGRATION_BIN=/absolute/path/to/lightpanda-x86_64-linux \
  go test -run '^(TestLightpandaIntegration|TestLightpandaRuntimeV1BridgeIntegration|TestLightpandaRuntimeV1StdioIntegration|TestLightpandaPoolRuntimeV1IntegrationC4)$' \
  -count=1 -v .

docker build --build-context contracts=../../apps/crawler/contracts \
  --platform linux/amd64 --target integration-test \
  -t jobseek-lightpanda-integration:amd64 .

LIGHTPANDA_INTEGRATION_BIN=/absolute/path/to/lightpanda-aarch64-linux \
  go test -run '^(TestLightpandaIntegration|TestLightpandaRuntimeV1BridgeIntegration|TestLightpandaRuntimeV1StdioIntegration|TestLightpandaPoolRuntimeV1IntegrationC4)$' \
  -count=1 -v .

docker build --build-context contracts=../../apps/crawler/contracts \
  --platform linux/arm64 --target integration-test \
  -t jobseek-lightpanda-integration:arm64 .
```

The Docker integration target is the supported way to download and exercise
the checksum-pinned binary. Direct host execution is intentionally not treated
as lifecycle-safe because it lacks the required PID and network isolation.

Successful output has this shape:

```json
{"ok":true,"result":{"status":200,"final_url":"http://127.0.0.1:8080/fixture","html":"<html>...</html>","expression":"Fixture"}}
```

Exit status 0 is reserved for a complete `ok:true` response. If JSON escaping
expands an otherwise field-bounded result beyond the final output limit, the
CLI emits a small `ok:false` response when possible and exits 1. Serialization,
short-write, and writer failures also exit nonzero.

URL and expression inputs are bounded before use, and child logs are bounded as
they are written. HTML and expression values are necessarily materialized by
the CDP client before their output-size checks run; the required 512 MiB
container memory limit is the hard allocation bound. Normally an oversized
result returns an error, but an extreme page can instead hit the container
limit. The runtime-v1 bridge preserves this inherited pre-materialization
behavior; streamed CDP decoding is deferred and is not implied by the caller's
smaller evaluation-result ceiling. Error strings are truncated only after the
originating library creates them. Successfully encoded JSON is buffered and
size-checked before its single output write.

This CLI is not a secret-handling interface. Its URL and expression are visible
in process arguments, and final URLs, browser-derived values, child logs, and
errors can be returned verbatim. URL userinfo is rejected, but callers must not
put credentials, tokens, or other secrets in arguments, query strings, page
content, or expressions.

## Scope exclusions

This pilot intentionally has no queues, crawler wiring, browser fallback,
resident or shared browser-process pooling, proxy or authentication support,
request interception,
`networkidle` waiting, `stopLoading`, iframe capture, nightly binary, deployment,
or observability integration. The runtime-v1 bridge is exercised only against
loopback fixtures and has no reusable production evaluation-privacy sealer or
destination/subresource policy. It is not a production service or production
browser-security boundary.

The framed handler is reusable protocol/core code only. It must not run for a
public URL inside the credentialed, root, host-network production browser
worker. Production use remains blocked on a separately reviewed
credential-free, non-root isolated Go service/container/cgroup; browser-wide
egress enforcement for private addresses, redirects, subresources,
WebSockets, and in-page fetch; and a Python supervisor with immutable
assignment, cancellation, backpressure, health, and rollback behavior. A
future service may wrap one framed request and response per connection around
this handler and place fresh one-shot Lightpanda children behind a bounded
origin-fair pool. None of that authority is introduced here.

## Native c4 implementation smoke

Pull requests also build a credential-free, build-tagged Go smoke image, a
production-shaped Python 3.13 + Playwright/Chromium image, and an exact local
fixture image on native Linux amd64 and arm64 runners. The controller runs one
Go arm followed by one Python arm at c4. Each arm receives the same 16-task,
eight-origin, two-wave workload: four render-only B0 tasks and four B1 title
evaluations per wave, with modes swapped by origin in the second wave.

The Python arm warms exactly four Playwright drivers before its measured clock
and creates a fresh browser, context, and page for every task. The Go arm creates
one fixed four-worker pool before its measured clock and retains the existing
fresh one-shot Lightpanda lifecycle per task. Both measured containers are
limited to one CPU, 1 GiB memory with no additional swap, 512 PIDs, and 256 file
descriptors. The fixture runs outside that cgroup on a unique Docker internal
network and is separately bounded. The controller verifies the effective
cgroup-v2 limits and aggregate counters, exact image IDs, stopped-container
isolation, task/oracle conservation, browser cleanup, and zero labeled-resource
postflight. Only its allowlisted report is retained.

This is an implementation and containment smoke, not comparative performance
evidence. It does not execute the counterbalanced repetitions, retain partial
failed-arm evidence, calculate ratios, or make a RAM-density/admission claim.
Those belong to the still-open fixed-RAM evidence issue and require a separate
frozen protocol plus independent review. The smoke grants no crawler, queue,
database, Murmur, or production authority.

Each measured runner publishes an empty, private initial marker only after its
release-signal handler is installed. The controller attests the runner's host
PID identity and sole cgroup membership, starts the sampler, and sends
`SIGUSR1`. After the workload and all browser resources are torn down, the
runner publishes a distinct final marker and waits for `SIGUSR2`; only then
does it emit its single JSON report and exit. The controller never enters the
measured cgroup, and stale markers cannot satisfy the other phase.

## Frozen Stage B2 density evidence

`density/schedule.v1.json` freezes the comparative protocol independently of
the pull-request smoke. It runs one diagnostic c1 pair, five counterbalanced
c4 pairs, and five counterbalanced c8 pairs, for exactly 22 sequential arm
records. Every arm gets a fresh fixture, internal network, measured container,
and cgroup with the same one-CPU, 1 GiB no-swap, 768-PID, and 256-file limit.
There are no retries, substitutions, adaptive profiles, public origins, or
production queue/database access.

Admission requires both implementations to pass all five c4 arms and Go to
pass all five c8 arms. If both implementations pass c8, the paired median
Python/Go elapsed ratio must be at least 1.25 with four strict Go wins, and the
paired median Go/Python memory ratio must be at most 0.80 with four strict Go
wins. If both c8 median peaks remain below 70% of the cap, the result is
inconclusive because the RAM ceiling was not exercised. If Python instead has
at least three kernel-confirmed c8 memory-pressure failures and no other c8
failure class, every Go c8 peak must remain strictly below 80% of the cap.

The executor checkpoints all 22 ordered slots in a private atomic report.
Cleaned runtime, correctness, timeout, and memory-pressure failures are
retained and later arms continue. Cleanup, containment, protected-host-health,
or measured-process-residue failures abort the run, leave unstarted slots
explicitly `not_run`, and can never produce admission. Failed memory samples
may exceed the cgroup limit by at most one recorded host page; values are never
clamped. Evidence output contains only closed identifiers, exact image IDs,
integer timings, and aggregate cgroup counters—never raw logs, URLs, PIDs,
cgroup paths, or service names.

Run the frozen protocol only on an isolated Linux Docker host after building
the three density images from the same reviewed source commit. Repeat
`--protected-container` for any colocated service whose identity, start time,
restart count, and boundary health must remain unchanged. These checks run
before and after every arm; resource isolation remains the protection against
transient interference within an arm:

```sh
test "$(git rev-parse HEAD)" = "$REVIEWED_SOURCE_COMMIT"
git diff --quiet
git diff --cached --quiet
test -z "$(git ls-files --others --exclude-standard)"

docker build \
  --build-context contracts=../../apps/crawler/contracts \
  --build-arg "SOURCE_COMMIT=$REVIEWED_SOURCE_COMMIT" \
  --target density \
  --tag jobseek-lightpanda-density:evidence \
  .
docker build \
  --build-arg "SOURCE_COMMIT=$REVIEWED_SOURCE_COMMIT" \
  --file density/Dockerfile.python \
  --tag jobseek-python-chromium-density:evidence \
  density
docker build \
  --build-arg "SOURCE_COMMIT=$REVIEWED_SOURCE_COMMIT" \
  --file density/Dockerfile.fixture \
  --tag jobseek-density-fixture:evidence \
  density

sudo --non-interactive python3 density/evidence_runner.py \
  --go-image jobseek-lightpanda-density:evidence \
  --python-image jobseek-python-chromium-density:evidence \
  --fixture-image jobseek-density-fixture:evidence \
  --source-commit "$REVIEWED_SOURCE_COMMIT" \
  --protected-container protected-service \
  --timeout-seconds 180 \
  --output /tmp/go-lightpanda-density-evidence.json
```

The final process status reports whether the frozen execution completed; the
report's closed `summary.status` is the separate admission decision. An
`inconclusive`, `not_admitted`, or `invalid` summary must not be promoted into
a production-routing decision.
