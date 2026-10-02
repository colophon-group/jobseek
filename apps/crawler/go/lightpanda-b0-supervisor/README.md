# Native producer cold initialization

`lightpanda-b0-supervisor producer --initialize-cold ABSOLUTE_DECISION_FILE SHA256`
establishes only an empty native producer authority/sentinel pair at the explicitly
configured epoch. It transfers no tasks, reserves no epoch, and grants no ordinary
ownership, journal closure or host readiness. Normal startup still refuses owned
Redis with an absent activation sentinel.

The ADR006 host coordinator must first independently verify all-writer quiescence,
the exact retained 0045 ordinary restoration decision and reversal at reserved R,
completed B0 restoration and tombstone/sentinel clearing, and the immutable candidate
release/image. Hashes in this delegated request identify that evidence; this producer
has no database credentials and cannot independently verify SQL or the host receipt.
The host integration and old immutable rollback binary compatibility remain separate
admission requirements. Do not invoke this command as an alternative deployment path.

The decision is a closed, canonical JSON object (sorted ASCII keys, compact encoding,
no newline, at most 8192 bytes). It contains:

- `schema`: `jobseek.lightpanda.producer-cold-initialization/v1`;
- `all_writers_receipt_sha256`, `ordinary_restoration_plan_sha256`, `reversal_sha256`:
  exact lowercase SHA256 identities of independently checked retained evidence;
- `source_revision`: the exact 40-character revision compiled into this producer;
- `runtime_image`: the independently verified
  `ghcr.io/colophon-group/jobseek-crawler@sha256:…` release;
- `source_epoch`: original forward E, with `0 < E < R`;
- `routing_epoch`, `namespace`, `shard_id`, `cohort`: the exact configured R/target;
- `lua_sha256`: the compiled, reviewed queue Lua digest.

The caller delegates a regular, single-link, root-owned `0640` file with group 10001,
through a protected root-owned parent path traversable by that group. The producer
runs as fixed UID 10001 with client UID 0, the fixed production socket and a private
producer-owned `0700` runtime directory. There must be no existing producer socket.
A lifecycle flock excludes the serving native producer and other initializers.
The command uses a 30-second deadline and performs no automatic retry.

Before effects, the producer fsyncs the exact decision in
`.cold-initialization-v1-SHA256.request` under its runtime directory. It reuses the
normal native preparing/active sentinel bootstrap, audits the empty queue, requires
acknowledged Redis SAVE, repeats the complete authority/empty-queue readback and
fsyncs an immutable `.complete` record containing the same decision. Exact completed
retry verifies the current empty pair without repeating initialization or SAVE.
Interrupted preparation or SAVE can recover only with the same retained request and
the normal valid sentinel/Redis pair. Missing or changed history, sentinel loss,
nonempty queues and orphan authority require explicit cold recovery; they are never
silently adopted. Preserve both records with the native runtime directory through
host recovery. The persistent `.lifecycle-v1.lock` file contains no authority; its
kernel lock is held for the entire serving or initialization process.

Linux CI runs the actual UID-10001 command before prior-B0 reactivation planning,
checks wrong approval and rehashed runtime drift, exercises real RDB reload, and
then tests actual native task application and CLI SIGKILL recovery. These generated
host evidence labels do not establish production release or quiescence evidence.
