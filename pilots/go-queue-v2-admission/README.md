# Queue-v2 admission-before-claim pilot

This Go 1.24 module is an inactive, non-production composition pilot. It binds
the accepted origin-aware sitemap worker pool to the inactive queue-v2 Redis
candidate and real lease supervisor without adding queue enumeration,
scheduling, production credentials, deployment, or cohort authority.

The exact accepted parent inputs are:

- queue-v2 Redis client and lease supervision through composite parent
  `594c4bb9a0ca4e401901c013eec99a112bfc7800`;
- origin-aware `worker.Pool` through parent
  `67924db2a657f64273e99f2690297656356ab224`.

`go.mod` deliberately requires both parents at `v0.0.0` and uses local
replacements for `../go-http-sitemap` and `../../apps/crawler/contracts`. The
pilot must not modify either parent module tree.

## Lifecycle

Each submitted `queueworker.Candidate` is still unclaimed while it waits in the
bounded local pool. Its immutable value is carried on a copied job with a
private typed context key, so duplicate task IDs never share an ID-indexed
side table. One fixed Go-owned queue route and one lease TTL apply to the whole
Runner.

The reserved worker path is strictly:

```text
reserve worker + origin permit
  -> claim exact candidate
  -> validate grant and start supervision
  -> execute with derived lease context and full fence
  -> Stop and join supervision
  -> exact-fence complete or reschedule CAS
  -> publish bounded local result
  -> release pool slot
```

Claim rejection, ambiguous transport, an invalid grant, lease loss, parent
cancellation, timeout, or panic never produces a terminal queue retry. The
inflight record is left for the queue-v2 reaper. Terminal rejection or
transport ambiguity is final for this execution and is never retried. Closing
the Runner closes the claim gate before closing the pool, so already-started
claims may resolve while queued processors do not begin new claims.

Result publication and slot release retain `worker.Pool` semantics. Callers
must continuously drain `Runner.Results()`; bounded result backpressure can
otherwise delay slot release and graceful shutdown.

## Verification

From this directory:

```bash
go test ./...
go test -count=10 ./...
go test -race ./...
go vet ./...
test -z "$(gofmt -l .)"
```

The exact Redis lifecycle composition is fail-closed and opt-in. It requires a
dedicated Redis instance whose database 15 starts empty:

```bash
QUEUE_V2_ADMISSION_REDIS_ISOLATED=1 \
QUEUE_V2_ADMISSION_REDIS_URL=redis://127.0.0.1:6381/15 \
go test -count=1 -race ./...
```

The integration test loads the repository's exact `lifecycle.lua`, creates a
unique namespace, and deletes only the seven keys exposed by that candidate
client. It asserts database 15 is empty before and after the run.
