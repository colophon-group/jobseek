# Queue-v2 bounded due-feeder pilot

This Go 1.24 module is an inactive, non-production composition pilot. It adds
one bounded read-only due snapshot in front of the unchanged
`go-queue-v2-admission` runner. It has no continuous poller, durable cursor,
registration writer, new Lua operation, deployment, credentials, or traffic
authority.

The ordering is deliberately:

```text
Redis TIME
  -> bounded ZRANGE of due ready members
  -> pipelined exact config-revision HGET
  -> resolve every immutable (task ID, revision) pair
  -> validate the admitted non-browser HTTP sitemap profile
  -> submit unclaimed candidates
  -> existing runner reserves global/origin capacity
  -> existing queue-v2 claim CAS
  -> execute under the supervised fence
```

Enumeration is advisory and changes no Redis state. Revision rotation, route
rotation, a duplicate page, and other races remain the existing claim CAS's
responsibility. This module intentionally does not add a `claim_next`, local
deduplication database, or once-only guarantee. In particular, because the
queue contract has no attempt nonce, an old `(task ID, revision)` observation
may legitimately claim again after an authoritative reschedule of that same
revision. That is queue lifecycle behavior, not feeder duplication.

The task-ID, revision, page, and aggregate-byte budgets are application-level
validation performed immediately after go-redis decodes each bounded reply.
They are not advertised as a network-frame or Redis allocation limit.

`CandidateResolver` is injected because the repository does not yet have a
durable immutable sitemap-configuration history. It must resolve only the
requested numeric revision and return a value-owned `sitemap-http-v1`
candidate. The feeder verifies exact identity, rejects browser-required
profiles, and validates an absolute userinfo-free HTTP(S) sitemap URL and the
full accepted sitemap configuration before any page item is submitted. It
does not invent a runtime-v1 extension or treat `board_url` as a sitemap URL.

The feeder resolves the complete bounded page before submitting its first
item. `Close` cancels active one-shot calls and closes the admission runner's
claim gate. Callers still must drain the runner's bounded `Results` stream.

## Verification

From this directory:

```bash
go test ./...
go test -count=20 ./...
go test -race ./...
go vet ./...
test -z "$(gofmt -l .)"
```

The real-Redis test requires an exact isolated instance whose database 15
starts empty:

```bash
QUEUE_V2_DUE_FEEDER_REDIS_ISOLATED=1 \
QUEUE_V2_DUE_FEEDER_REDIS_URL=redis://127.0.0.1:6382/15 \
go test -count=1 -race ./...
```

It loads the repository's exact queue-v2 lifecycle Lua only to build a valid
fixture. The source itself runs `TIME`, `ZRANGE`, and pipelined `HGET` reads.
The test snapshots all seven exact namespace keys before and after each read,
then deletes only those keys and requires database 15 to be empty.
