# Dormant isolated Chromium service boundary v1

This package freezes a source-only Chromium execution boundary for #8235. It
consumes the runtime-v1 `BrowserExecutionInput`, `BrowserAssignment`,
`BrowserPlan`, and `BrowserResult` messages frozen by #8247. It does not launch
Chromium, open CDP or a listener, contact an origin, claim queue work, emit
production metrics, build an image, alter deployment, or activate routing.

The Go package is
`github.com/colophon-group/jobseek/apps/crawler/contracts/v1/chromiumservice`.
It deliberately lives in the existing contracts module and adds no dependency
or IDL. The only implementation supplied here is a deterministic fake used by
the network-free conformance tests.

## Configuration and isolation declaration

`config.schema.json` and `DecodeConfig` define the same closed configuration.
The decoder accepts one JSON object of at most 32 KiB, rejects unknown fields,
null values, trailing data, zero or unbounded limits, and never reads inherited
environment. Configuration contains:

- the fixed `chromium` backend and `chromium` service lane;
- immutable OCI-image and Chromium-binary SHA-256 digests;
- bounded Chromium and CDP-client release identifiers;
- one private Unix socket below `/run/jobseek/chromium`, never TCP;
- an egress-policy revision reference without credentials;
- concurrency, active-session TTL, shutdown grace, process age, RSS, target,
  PID, file-descriptor, socket, origin-operation, request, transfer-byte,
  task-recycle, and writable-tmpfs ceilings; and
- non-root UID/GID, read-only root, no-new-privileges, all capabilities dropped,
  and `runtime/default` seccomp declarations.

Database, Redis, Typesense, R2, proxy/CDP credentials, arbitrary environment,
deployment, and raw process-launch fields are absent. This source boundary can
validate an isolation declaration; a separate reviewed child must prove that a
future immutable image and runtime actually enforce cgroups, mounts, endpoint
permissions, egress, seccomp, and the declared identities.

## Pre-provider binding

`Service.Execute` clones the complete protobuf input before provider work. It
then rejects a missing plan or assignment, a non-v1 plan, an empty target,
empty/unknown/duplicate capabilities, an incorrect derived capability class,
anything other than Chromium backend and Chromium lane, an invalid routing
revision, and plan counts above the configured limits.

The cloned assignment is deterministically serialized and SHA-256
fingerprinted. A `BoundTask` returns only further copies of its plan and
assignment. The provider must echo the exact fingerprint with its outcome.
Caller or provider mutation therefore cannot change the bound backend, lane,
class, or revision. A mismatch is a protocol failure: authoritative output is
discarded, readiness closes, and recycle is requested after active work drains.

## Exactly one provider and session

`ChromiumProvider` is one combined Chromium provider/supervisor interface. No
provider registry or Lightpanda type exists in this package. After assignment,
process, capacity, and capability preflight:

1. missing provider capabilities return the exact sorted
   `BrowserUnsupported.capabilities` set before session or origin work;
2. one `OpenSession` creates a fresh task session;
3. that session receives one `Execute` call; and
4. the session receives one bounded `Close` call.

There is no loop, same-backend semantic retry, sibling backend, fallback, or
second invocation path. Transport reconnect beneath a future provider remains
outside this boundary and may not repeat semantic origin work.

The provider outcome contains exactly one `BrowserSuccess` or message-free
`ProviderFailure` plus the bound fingerprint. The service constructs the
runtime-v1 contract version and Chromium result backend. Unknown or mismatched
failure code/disposition pairs, an empty/double outcome, a fingerprint
mismatch, or success attached to failure become fail-closed `INTERNAL` errors.
No provider message or value is copied to the result. Thus unsupported and
error results contain no authoritative HTML, action, capture, evaluation, or
success payload.

Both the serialized success envelope and its logical transfer payload are
bounded by `max_transfer_bytes`. Logical accounting validates complete ordered
chunk manifests, overflow-safe chunk totals, inline sizes, and artifact-handle
sizes. An artifact identity is charged once only when every repeated handle has
identical media type, size, digest, and redaction metadata; inconsistent reuse
fails closed. This prevents small protobuf metadata from concealing oversized
out-of-band artifacts or wrapped manifest totals.

## Cleanup, process lifecycle, and health

Cleanup uses a background context bounded by `shutdown_grace_ms`, including
when the execution context is cancelled. The first `Shutdown` call fixes one
absolute, non-extendable grace deadline and cancels every active service-owned
task context. Each task then closes its session against that same deadline;
later shutdown or cleanup calls cannot restart the grace window. Cleanup
failure overrides and discards success, returns a typed fail-closed error,
marks the last cleanup unsuccessful, and requests recycle.

Completion removes the task from the active count before evaluating its
post-close snapshot. A remaining session or target is therefore a leak,
overrides any apparent success with a fail-closed error, and requests exactly
one immediate recycle when the service becomes idle. Closed recycle reasons
also cover process age, RSS, process crash, failed health, cleanup failure,
protocol failure, and task count.

`Health` is bounded local state, not an endpoint. Every read refreshes the
provider's bounded, non-blocking supervisor snapshot outside the service lock,
then commits it only if the lifecycle generation is unchanged. A racing
lifecycle transition is retried once and otherwise reports fail-closed
`initializing`, never stale-ready. Idle crashes, pin changes, resource breaches,
and leaks therefore close readiness without waiting for another task. Health
reports only Boolean and numeric capacity/lifecycle values plus closed reason
enums. It never includes
socket paths, image/browser pins, hosts, assignment identifiers, origin data,
or provider error text. Readiness requires:

- live and healthy process state with exact pins;
- all configured resource ceilings respected;
- no leaked session or target;
- successful last cleanup;
- no recycle pending, shutdown, or exhausted concurrency; and
- a configuration already accepted by `New`.

`Shutdown` rejects new work and waits, without launching a goroutine or killing
a shared process, for cancellation and bounded closure of sessions already
owned by the service.

## Conformance and ownership

`fixtures/chromium_service/manifest.json` is canonical compact JSON with a
checked SHA-256 sidecar. Independent Go and Python evaluators hard-code the same
ordered 84-case registry. The network-free corpus covers all capability
classes, strict assignment and configuration failures, exact unsupported
preflight, one-call conservation, mutation/fingerprint faults, typed failures,
partial-output rejection, active cancellation/shutdown and cleanup override,
post-close leaks, live idle-health transitions, declared-transfer overflow,
explicit no-retry/fallback/second-call and origin-before-validation failures,
cross-task cookie/storage/target/context/assignment isolation, process limits,
health, and between-task recycle.

This boundary remains subordinate to:

- #7961 for the unified executor and caller-facing routing API;
- #7960 for the separate Lightpanda service lifecycle;
- #7940 and #8241 for promotion and freeze decisions;
- #8243 for capability assignment evidence;
- #7939 for future publisher/TDM/SSRF/egress enforcement;
- #7936 for measured Python-versus-Go crawler cost at projected load; and
- #7966 for any optional future Chromium retirement decision.

Chromium remains first-class while assigned demand exists. No result, idle
period, unsupported capability, or cost observation in this package can
self-promote Lightpanda, activate fallback, or retire Chromium.

## Deferred activation gates

A later child must separately own and review an immutable Chromium image,
unprivileged process launcher, private endpoint, cgroup and restart ownership,
egress enforcement, synthetic integration environment, metrics, and packaging.
Only after offline replay and the external promotion/freeze gates pass may a
separate change route one exclusive canary cohort. Any second provider call,
semantic retry, cross-backend fallback, assignment mutation, wrong backend,
public CDP reachability, credential inheritance, origin-before-validation,
unbounded growth, cross-task state leak, cleanup failure, partial authoritative
error output, or impact on another service lane freezes rollout.
