# Go + Lightpanda lifecycle pilot

This is a deliberately small, nonproduction experiment for driving Lightpanda
0.4.0 through CDP with `chromedp` 0.14.2. One invocation accepts one absolute
HTTP(S) URL and one synchronous JavaScript expression. It returns bounded JSON
containing the top-level document's final response status, final URL,
`<html>` outerHTML, and the expression value.

The same package also contains a dormant in-process implementation of the
runtime-v1 `lightpandaadapter.Runner`. It maps only B0 render or B1 render plus
one synchronous evaluation declared to have no network effect onto exactly
one existing one-shot lifecycle. It is not exposed by the CLI and adds no
service, endpoint, queue, persistence, fallback, deployment, or production
routing.
The adapter still requires an injected evaluation-privacy implementation;
only a loopback-fixture sealer exists in tests.

The package also contains a non-authoritative bounded execution pool for the
fixed-RAM pilot. The pool admits immutable runtime-v1 inputs into a fixed set
of workers, dispatches different origins fairly, and permits at most one task
per origin at a time. Every active slot still owns a separate one-shot
Lightpanda process and the same cleanup proof below. Processes are not reused
or shared between tasks. The pool has no CLI, queue, crawler, or production
entry point.

The bridge preserves the pilot's stricter 512 KiB HTML ceiling and reports an
oversized document as the adapter's closed `RESOURCE_LIMIT` result. B1 uses
the smaller of the plan's `max_result_bytes` and the pilot's 64 KiB ceiling.
A typed cleanup-verification failure remains authoritative over a concurrent
timeout or cancellation; other provider details are reduced to message-free,
fail-closed adapter errors.

The runner starts a new Lightpanda process for its single task on a chosen free
loopback port. Concurrent in-process runners retain each allocated port until
that process has been cleaned up, preventing sibling tasks from selecting the
same close-then-bind port:

```text
lightpanda serve --host 127.0.0.1 --port <port> --log-level error
```

It polls `http://127.0.0.1:<port>/json/version`, accepts only a `ws` endpoint
whose literal host is `127.0.0.1` and whose port is the allocated port, checks
that the child is still running before and after readiness, and creates a fresh
remote allocator and target. After every outcome, including timeout, it
terminates the Lightpanda process group, waits/reaps the leader, and verifies
that both the process group and listener are gone. A cleanup verification
failure makes that one-shot invocation fail.

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
  go test -run '^(TestLightpandaIntegration|TestLightpandaRuntimeV1BridgeIntegration|TestLightpandaPoolRuntimeV1IntegrationC4)$' \
  -count=1 -v .

docker build --build-context contracts=../../apps/crawler/contracts \
  --platform linux/amd64 --target integration-test \
  -t jobseek-lightpanda-integration:amd64 .

LIGHTPANDA_INTEGRATION_BIN=/absolute/path/to/lightpanda-aarch64-linux \
  go test -run '^(TestLightpandaIntegration|TestLightpandaRuntimeV1BridgeIntegration|TestLightpandaPoolRuntimeV1IntegrationC4)$' \
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
