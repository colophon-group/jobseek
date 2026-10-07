# Lightpanda B0 mTLS service boundary

This contract defines the one-shot network boundary between the Python-owned
Lightpanda B0 claimant and the Go render process. A dedicated claimant command
is available in the slim crawler image, but it fails closed unless explicitly
enabled with complete route and mTLS configuration. A separate manual workflow
can install one dormant renderer on the existing ARM64 Murmur host. Its
internal-only bridge has no host port or default route, and no crawler or board
route points at it.

Python remains authoritative for claiming, leases, retry policy, parser
configuration, parsing, PostgreSQL writes, and queue transitions. The Go
process accepts only an already-claimed runtime-v1 render plan and returns only
a sanitized `BrowserResult`. It cannot select work, retry origin access, fall
back to Chromium, parse a posting, or write state.

## Fixed endpoint and TLS identity

The service listens on caller-supplied configuration, never inherited network
or credential environment:

```text
go-lightpanda --runtime-v1-service \
  --listen 0.0.0.0:9443 \
  --service-ip 10.0.0.5 \
  --deployment-deny-cidr <service-private-ip>/32 \
  --deployment-deny-cidr <host-public-ip>/32 \
  --deployment-deny-cidr <project-network-cidr> \
  --tls-cert /run/credentials/server.pem \
  --tls-key /run/credentials/server-key.pem \
  --tls-ca /run/credentials/ca.pem \
  --tls-ca-sha256 <lowercase DER SHA-256> \
  --client-leaf-sha256 <lowercase DER SHA-256> \
  --client-spki-sha256 <lowercase SPKI SHA-256>
```

The listen address is exactly `0.0.0.0:9443`, used only as the container-local
bridge bind. It is never a certificate identity, client endpoint, or authority
to publish the service publicly. The separate service IP must be a canonical
literal private IPv4 address that is neither loopback nor unspecified. A later
activation must publish port 9443 only on that private host IP and admit only
the crawler's private source address. The dormant deployment has no host port;
its isolated bridge has no host gateway or host route. A one-shot probe runs
inside the renderer network namespace and requires the exact TLS 1.3
`certificate_required` rejection while still verifying the server's
`10.0.0.5` identity. Session tickets are disabled, and the only ALPN is
`jobseek-lightpanda-b0/1`. Both peers load one dedicated CA certificate and
verify its DER SHA-256 before opening a connection.

Each trusted deployment address or project prefix is supplied once with the
repeatable `--deployment-deny-cidr` startup flag. The inventory is nonempty,
canonical, unique, and bounded. Overlapping host/project entries are accepted
only when each is fully covered by the version-controlled registry baseline;
uncovered overlaps are rejected. Baseline-covered entries (including the
service's private address) are reconciled without adding an overlapping
Lightpanda CIDR. Every other entry must be a globally routed deployment prefix,
and at least one such uncovered prefix is required. Service construction
reconstructs the immutable deny policy and verifies every supplied entry before
cgroup attestation, TLS loading, listener creation, or any Lightpanda child.
Deployment tooling remains responsible for supplying the complete live
inventory. The baseline-only policy used by the standalone CLI and stdio
fixture cannot qualify service mode. The service accepts only a render
execution bound to the exact same qualified policy; a baseline-only or
mismatched execution is rejected at construction. Inventory values are trusted
deployment configuration, never request fields, and validation failures do not
echo them.

The server leaf has exactly one IP SAN: the configured private IPv4 service IP.
It has exactly the `serverAuth` extended usage and an explicit valid
BasicConstraints extension with `CA=false`. The crawler leaf has exactly one SAN:
`spiffe://jobseek/crawler/lightpanda-b0`. It has exactly the `clientAuth`
extended usage and the same explicit BasicConstraints `CA=false`. DNS, email,
additional IP/URI SANs, additional extended usages, unknown usages, and
unsupported raw GeneralName alternatives are rejected. The server also
requires exact crawler leaf and SPKI pins; the Python client requires exact
server leaf and SPKI pins. Rotation therefore updates the mounted certificate
and its reviewed pins as one deployment change. Keys and pins are never
embedded in the image or task.

## Compiled Dayforce session conversation

The Go ordinary worker can select `jobseek.lightpanda.dayforce-session/v1` on
the same pinned TLS connection and C4 slot. This separate provider conversation
accepts the exact typed canonical JSON request in `dayforcesession`, followed
by the empty opening marker. It carries a configuration fingerprint, canonical
Dayforce listing identity, expected HTTP-bootstrap site identity, offset overlap
and bounded deadline. It accepts no script, request headers, proxy configuration,
credential paths or arbitrary origin URL. Generic B0/B1 constructors continue to reject captures, actions and
origin-contact evaluation. The navigation-only Go service additionally supports
the bounded main-document response capture described below.

One fresh Lightpanda target captures the first correlated main-frame search
request/response. Its CSRF lease stays in controller memory and is erased on
cleanup. Only public site identity and canonical publisher-policy evidence are
returned in the ready frame. The worker chooses each search offset and retains
the established retry, overlap, completeness, projection and persistence rules.
The controller compiles each POST on that same target; page frames carry bounded
body bytes, status, exact source URL and policy signals. Sequences must increase,
the first offset is zero, offsets advance by `25-overlap`, and at most six
attempts can use one offset. Disconnect cancels an active fetch. A finish command
produces success only after target/process cleanup; unproved cleanup poisons the
resident service. Partial pages alone grant no inventory or write authority.

Requests are bounded to 16 KiB, commands to 1 KiB, page bodies to 1 MiB and frames
to 2 MiB. Sessions run within the held claim deadline and a 600-second ceiling;
they retain the existing TLS identity, egress policy, C4 slots and 1-GiB/no-swap
service limit. This path has local Go/TLS and actual Python retry/policy reference
proofs. Installed Lightpanda I/O, registry admission, canonical worker effects
and production deployment must be qualified before admitting Dayforce ownership.
It does not implement generic `ExecutionFrame` resumption or an origin ledger.

## B0/B1 one-shot wire order

The ordinary Go worker also uses `jobseek.lightpanda.feed-session/v1` on the
same pinned connection and C4 reservation. Its strict typed request binds one
HTTPS feed, configuration fingerprint, pagination parameter/sequence, readiness
and navigation budget. It accepts no scripts, headers or proxy credentials.
One fresh Lightpanda process and target retain cookies across feed pages.
Each sequenced page returns the exact bounded final main-document bytes and
publisher signals, including XML and CDATA. Page results remain provisional;
successful finish is returned only after target/process disposal. Disconnect
cancels active navigation. The client requires the matching final sequence and
EOF before granting a complete inventory to the native worker.

Requests are at most16 KiB, commands1 KiB, raw bodies2,000,000 bytes and result
frames2 MiB. The session ceiling is600 seconds. The existing deny policy, mTLS
identity, C4 capacity and memory limits apply. Generic pagination is bounded by
its compiled options; the default WordPress traversal also respects the native
50,000-job bound. Failed browser attempts discard the whole inventory, while
direct HTTP feed streams preserve previously accepted complete200-job batches.
Publisher reservations and failed/repeated/limited pages never finalize absence.
Proxy feed configurations remain unadmitted until trusted transport and installed
proof are available. This conversation does not implement generic resumption.

Each connection has this exact order:

1. The server sends one canonical unsigned-varint frame containing strict JSON:

   ```json
   {"protocol":"jobseek.lightpanda.service/v1","runtime_contract":"crawler.runtime/v1","mode":"b0","capacity":4,"memory_max_bytes":1073741824,"memory_swap_max_bytes":0}
   ```

2. The client sends one existing runtime-v1 frame containing a nonempty
   `BrowserExecutionInput`.
3. The client sends the canonical empty frame `00`. This is application-level
   write closure, not a second request. Python asyncio TLS cannot half-close
   its write side while retaining the read side for the result.
4. Only after that closure marker passes does the server validate and execute
   the input once. A nonempty second frame, noncanonical empty frame, malformed,
   oversized, or truncated input is rejected before origin execution. Bytes
   already buffered after the empty marker are also rejected before execution.
   After the marker, a dedicated bounded read watches for peer closure or a
   delayed trailing byte and cancels the active one-shot execution immediately.
   Delayed trailing bytes can never become another request: the server invokes
   the executor at most once and closes after its one result.
5. The server sends one deterministically framed, sanitized `BrowserResult`,
   sends TLS closure, and closes the TCP connection. The client requires EOF
   after that result.

The hello is capped at 512 prefix-inclusive bytes. Input remains capped at the
adapter's 128 KiB payload plus three prefix bytes. Result remains capped at 2
MiB prefix-inclusive. The Python client accepts only the exact canonical hello
payload bytes shown above; reordered, whitespace-varied, escape-equivalent,
additional, missing, or mismatched encodings fail closed. The service is
constructed with the Go render-only adapter, which rejects B1 evaluation
before its one-shot runner can contact the origin.

The Python client accepts exactly a canonical immutable
`LightpandaB0Task`. It derives only a B0 runtime-v1 plan: HTTPS target, `load`
wait, one `RENDER` capability, no evaluation/action/capture/session/header/TLS
override, and the task's frozen timeout and routing revision. It opens a raw
`asyncio` TLS socket to the literal IP. HTTP clients, proxy environment,
connection reuse, transport retry, and backend fallback are absent.

## Raw main-document feed capture

The navigation-only Go adapter can request `RENDER` plus `RESPONSE_CAPTURE`
with exactly one `CapturePlan`: `RESPONSE_BODY`, a nonempty capture ID and a
positive byte limit no greater than2,000,000. URL patterns, artifact-only output,
evaluation, actions, persistent sessions and request/transport overrides remain
refused before runner contact. The existing Python B0 client remains render-only.

The renderer waits for the correlated final main-document loading completion,
then returns its original CDP response bytes, including XML CDATA, in the existing
complete chunk manifest. It returns an empty HTML manifest alongside the capture;
it does not serialize an XML DOM or capture redirects/subresources. Final URL,
status and publisher-policy signals refer to that same document. The maximum
capture fits the existing2 MiB result frame. Missing bytes, identity changes,
oversized bodies or invalid chunk hashes fail closed. Authoritative process
cleanup still precedes success. This one-shot capability does not itself provide
affine pagination, proxy transport or ownership admission for RSS feeds.

## Native DOM and Inline actions

`jobseek.lightpanda.document-actions/v1` is a separate framed request on the
existing pinned mTLS reservation. It carries the existing navigation input,
request/configuration fingerprints and an ordered action list. The renderer
revalidates navigation through its render-only adapter and attaches actions
only after that preflight. Evaluation plans, captures and unrelated runtime
capabilities remain rejected by this path.

The admitted actions are `wait`, `evaluate`, `remove` and `dismiss_overlays`.
Removal uses `querySelectorAll(...).forEach(el => el.remove())`, preserving the
Python action's all-match behavior, including a successful empty match. Overlay
removal uses the same eight cookie/consent selectors. Selector text is JSON
encoded before evaluation; invalid selectors follow the existing optional or
required failure policy. Python defaults are preserved:
1,000 ms for a wait, ten seconds per action, optional failure unless
`required: true`. Evaluation invokes a function expression and awaits its
Promise, discarding the returned value. Required failure rejects the entire
inventory. Parent cancellation remains terminal even for an optional action.
Unknown controls and interactions retain their current owner. There are at most
32 actions, 32 KiB of encoded actions, 120 seconds per action and 300 seconds
of aggregate action time; complete requests remain within 128 KiB.

Each action operates in the same fresh target. The renderer recaptures and
correlates the main frame/loader/URL/policy before further actions and after the
pipeline. Publisher denial stops further actions and returns the declared
resource for normal policy classification. Invalid evidence or incomplete
capture fails closed. Only the existing typed document result crosses the
wire, after child/port cleanup; script exceptions never enter public diagnostics.
The sole bound response requires matching request/configuration fingerprints
and connection EOF. A disconnect or trailing request byte cancels execution.

The resident connection ceiling is 750 seconds: initial read/handshake budget,
the longest 600-second API conversation and cleanup/write time. Navigation and
action budgets remain independently bounded; the worker retains its existing
600-second task deadline and heartbeat lease. No shared browser or new queue
is introduced. Production admission requires the original source-bound plan
and supported staging/activation; the RPC alone grants no board ownership.

## Capacity and memory admission

Startup reads cgroup v2 `memory.max` and `memory.swap.max` and refuses to start
unless they are exactly 1,073,741,824 bytes and zero bytes respectively. The
hello attests those same constants. Runtime capacity is exactly four active
connections. Four semaphore slots are acquired before TLS and released only
after response/cleanup; the C+1 connection is closed immediately. There is no
internal work queue, prefetch buffer, result channel, shared browser session,
or resident Lightpanda process. Each admitted request retains the existing
one-shot process and cleanup semantics. Shutdown cancels every active execution
and closes admitted connections in handshake, partial-read, terminator-wait,
execution, or response state before waiting for their handlers. A
cleanup-unproved runner outcome poisons the entire resident service: admission
stops atomically, all connections are cancelled and closed, and the service
returns an error so its supervised container exits nonzero.

The dedicated `service` Docker target packages this binary with its exact
source-commit label. Its fixed `setpriv` launcher starts with only `KILL`,
`SETGID`, and `SETUID`, clears supplementary groups, changes to UID/GID 10001,
places only those capabilities in the controller's inheritable and ambient
sets, asserts `no_new_privs`, and execs Go. The launcher has no DAC capability
and cannot read the UID-10001 mode-0400 server key before dropping identity.
The Go controller attests its exact UID/GID and capability masks before loading
TLS or listening.

For every render, Go changes the child to UID/GID 10002 with only group 10002
before a second fixed `setpriv` trampoline. That trampoline clears inheritable
and ambient capabilities, asserts `no_new_privs`, and execs the pinned
Lightpanda binary. Lightpanda consequently has zero permitted, effective,
inheritable, and ambient capabilities. Its retained bounding mask cannot be
regained under `no_new_privs`. Startup proves that this child cannot read the
server key, controller environment, memory, or open key descriptor; cannot
signal the controller; receives only the two fixed non-secret environment
entries; and inherits no descriptor above standard I/O. The controller keeps
`KILL` only for cleanup of the distinct-UID child process group.

The caller must enforce the exact bootstrap, 1 GiB/no-swap cgroup, PID limit,
read-only filesystem, exact three-capability allowlist, private network
namespace, UID-10002 scratch mount, certificate mounts, and restart policy.

## Activation blockers

The dormant process remains inactive for crawler traffic until separate
reviewed slices provide all of:

- production deployment wiring for the Python capacity-reserving claimant;
- an external default-deny egress boundary covering host/public/private and
  project ranges in addition to the service-qualified browser deny policy;
- fixed 1 GiB/no-swap service containment and production observability; and
- a one-board B0 canary with frozen assignment and explicit rollback.

The manual deployment uses `/home/deploy/.local/share/jobseek-lightpanda`, the
separate `jobseek-lightpanda` Compose project, one `renderer` service, and an
internal `172.30.94.0/29` bridge. It retains only the CA, server certificate,
server key, and public pins; no client private key reaches Murmur. The paused
legacy Murmur containers remain stopped and byte-for-byte unchanged. Do not
expose port 9443 or route a board based on this document alone. The dormant
container uses bounded `on-failure:3` restart behavior and bounded local logs;
neither a reboot loop nor unbounded log growth is accepted.
