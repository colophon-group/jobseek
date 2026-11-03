# Production Lightpanda renderer

This Go module runs the self-hosted renderer used by production crawler workers.
Production uses the `service` image target and `--runtime-v1-service`. Go workers
retain queue, ownership, scheduling and canonical persistence authority; this
service executes bounded browser work. The production migration is complete:
see the [delivery checkpoint](../../docs/27-go-lightpanda-continuation-plan.md).

## Binary and deployment

[lightpanda-release.json](lightpanda-release.json) is the source of truth for the
upstream version, immutable OCI index, platform manifests, executable checksums
and matching official release assets. The Dockerfile copies only the official
executable, verifies its SHA-256 for amd64/arm64, and uses the pinned Debian base.
Moving nightly tags and release asset URLs are never build inputs. Retain the
previous promoted Jobseek digest for supported reversal. Apt packages are not
snapshot-pinned, so the entire image build is not reproducible.

The [renderer workflow](../../.github/workflows/lightpanda-renderer.yml) qualifies
both native Linux architectures, including real-binary semantic fixtures,
egress/child isolation, race tests and production image builds. The
[native whole-lane comparison](../../.github/workflows/crawler-native-whole-lane.yml)
checks complete canonical work and resources against the retained offline oracle.
A new upstream pin requires both architecture suites and whole-lane qualification.

Use the [supported deploy workflow](../../.github/workflows/deploy-lightpanda-renderer.yml)
on exact `main`; it publishes an immutable ARM64 image and deploys under the
shared host mutation lock with source/image attestation and rollback. Honor
crawler deployment holds and coordinate the shared host/demo window before
rollout. Do not use a mutable tag, live rsync or an individual writer restart.
The historical `dormant-controlled-egress` deployment label remains a compatibility
identifier consumed by release verification; it does not describe rollout status.

## Serving and isolation

The [service contract](../crawler/contracts/v1/lightpanda-service.md) defines the
closed one-request mutual-TLS protocol. The service requires TLS 1.3, the fixed
ALPN, exact peer SAN/EKU, a fingerprinted CA and leaf/SPKI pins. Its private
certificate identity differs from its container-local bridge bind. The deployed
[Compose contract](../../deploy/lightpanda-renderer/compose.yml) and verified
inventory own the private publication, network and external egress policy.
There is no public service endpoint or unauthenticated render API.

The service attests exactly 1 GiB memory, zero swap and four active connections.
Every request owns a fresh Lightpanda process; browser processes are not reused.
Cancellation and shutdown close admitted work. Cleanup must reap the leader and
prove that its process group and listener are gone; unproved cleanup poisons
the resident service so its supervisor can replace it.

The controller runs as UID/GID 10001 with only the capabilities needed for its
privilege bootstrap and cross-UID cleanup. Browser children run as UID/GID 10002,
with no permitted/effective/inheritable/ambient capabilities and a fixed,
non-secret environment. They cannot read the controller-owned TLS private key.
Keep the dedicated PID/network namespaces, read-only filesystem, tmpfs, PID,
file-descriptor, memory and CPU limits. Never expose CDP or use host namespaces.

Browser destinations are checked against the registry baseline plus trusted
startup host/project CIDRs. Task data cannot change this policy. Baseline-only
construction cannot qualify service mode. Browser-internal checks complement
the external egress boundary; they are not OS containment for a compromised
browser. Service startup reconciles the reviewed inventory before opening TLS
or starting a child. Preserve publisher policy and all bounded response limits.

`verify.py <installed-environment> --allow-active` checks exact installed release,
image/process/network/cgroup boundaries and the expected TLS rejection without
a client certificate. That negative TLS proof does not establish rendered-page
semantics, provider coverage or throughput; use the relevant integration and
whole-lane evidence for those claims. Host verification uses the protected
operator workflow, never an agent-owned Docker socket.

## Local verification

Run from this module:

```sh
go test ./...
go test -race ./...
go vet ./...
go mod tidy -diff
python3 -m unittest test_release_pin.py
```

Unit tests need no browser. The Docker integration target is the supported way
to exercise the checksum-pinned executable in an isolated Linux environment.
The explicit library contexts prevent crawler credentials or the repository
root from entering this build context:

```sh
docker build \
  --build-context contracts=../crawler/contracts \
  --build-context api-sniffer-monitor=../crawler/go/api-sniffer-monitor \
  --build-context dom-detail=../crawler/go/dom-detail \
  --build-context jsonld-detail=../crawler/go/jsonld-detail \
  --build-context publisher-policy=../crawler/go/publisher-policy \
  --platform linux/arm64 --target integration-test \
  -t jobseek-lightpanda-integration:arm64 .
```

Use `linux/amd64` on a native amd64 runner. Positive fixtures have a test-only
loopback exemption absent from production; negative policy fixtures retain the
default baseline. Tests must not contact publishers. Direct host execution
lacks the required lifecycle isolation and is not an acceptance substitute.

## Offline interfaces

The one-shot CLI accepts one absolute HTTP(S) URL and a synchronous expression;
`--runtime-v1-stdio` accepts one bounded canonical frame and EOF, returning one
bounded deterministic result. These remain useful fixture interfaces. The
render-only framed adapter rejects evaluation before origin work. They do not
claim production queues, and their baseline-only egress policy is insufficient
for service deployment.

CLI URL/expression arguments and returned values/errors can be visible verbatim.
Never place credentials in arguments, query strings, page content or expressions.
Run fixture containers with the same reviewed isolation controls and explicitly
authorized destinations. JSON escaping, serialization and short writes fail
closed; status zero requires a complete successful response.
