# Lightpanda B0 mTLS service boundary

This contract defines the one-shot network boundary between the Python-owned
Lightpanda B0 claimant and the Go render process. A dedicated claimant command
is available in the slim crawler image, but it fails closed unless explicitly
enabled with complete route and mTLS configuration. There is no Compose
service, host unit, certificate, firewall rule, board assignment, deployment
secret, or production route.

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
deployment must publish port 9443 only on that private host IP and admit only
the crawler's private source address. TLS is exactly TLS 1.3, session tickets
are disabled, and the only ALPN is `jobseek-lightpanda-b0/1`. Both peers load
one dedicated CA certificate and verify its DER SHA-256 before opening a
connection.

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

## One-shot wire order

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

The dedicated `service` Docker target packages this binary but declares no
host deployment. Its caller must enforce the 1 GiB/no-swap cgroup, PID limit,
read-only filesystem, non-root identity, dropped capabilities, private network
namespace, certificate mounts, and restart policy.

## Activation blockers

This boundary is inactive until separate reviewed slices provide all of:

- production deployment wiring for the Python capacity-reserving claimant;
- Murmur certificate generation, protected delivery, rotation, and rollback;
- an external default-deny egress boundary covering host/public/private and
  project ranges in addition to the existing browser-level deny policy;
- fixed 1 GiB/no-swap service containment and production observability; and
- a one-board B0 canary with frozen assignment and explicit rollback.

Do not expose port 9443 publicly, deploy this target, or route a board based on
this document alone.
