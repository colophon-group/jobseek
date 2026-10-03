# Native producer cold lifecycle

`lightpanda-b0-supervisor producer --cleanup-cold ABSOLUTE_DECISION_FILE SHA256`
removes the restored source activation sentinel and exact rollback tombstone. It
requires producer mode `off`, fixed UID/GID 10001/client UID 0, the fixed socket path,
an absent socket and the same lifecycle lock used by serving and initialization.
It allocates no epoch and creates no task, producer owner or ordinary authority.

The host coordinator must authenticate the stopped source process, original host
flock, SQL restoration and zero source write fences, selected Redis generation,
complete writer quiescence and immutable producer release before delegating the
decision. The producer has no SQL credentials; receipt hashes identify those
independently checked predicates and cannot replace host checks. Native host
journal integration and actual installed command evidence remain required.

The root-owned single-link `0640` decision has group 10001 and uses canonical compact
JSON with no trailing newline, at most 8192 bytes. Its sorted fields are:

- `schema`: `jobseek.lightpanda.producer-cold-cleanup/v1`;
- `activation_sentinel_sha256`: exact existing source marker bytes;
- `all_writers_receipt_sha256`, `b0_restoration_plan_sha256`,
  `ordinary_restoration_plan_sha256`, `reversal_sha256`, `source_receipt_sha256`,
  `sql_cleanup_receipt_sha256`: exact retained, independently checked identities;
- `cohort`, `namespace`, `shard_id`, `source_epoch`: exact configured source N;
- `retirement_epoch`: already reserved R, with `0 < N < R`;
- `source_revision`: revision compiled into the producer;
- `runtime_image`: exact `ghcr.io/colophon-group/jobseek-crawler@sha256:…` release;
- `lua_sha256`: the compiled, reviewed lifecycle script digest.

Before effects, the command requires all source queue keys and the legacy guard
to be absent, checks the exact eight-field source tombstone, binds the existing
safe marker, and fsyncs `.cold-cleanup-v1-SHA256.request`. It clears the marker,
uses the reviewed atomic Lua to clear only that exact tombstone, requires Redis
SAVE and absent-authority readback, then fsyncs the same decision in `.complete`.
The command has a thirty-second deadline and performs no automatic retry.

Pending recovery accepts source absence only with the exact retained request.
Completed retry verifies absence without clearing or saving again. A different
owner, source marker, receipt, queue/guard, changed or missing history, cancelled
context or denied SAVE never grants completion. It does not repair Redis loss or
adopt authority created after cleanup. Keep both records in the runtime directory
through host recovery. Library tests exercise real Redis/Lua/SAVE and boundary
recovery; they do not establish installed command or host deployment admission.

## Initialization at the reserved retirement epoch

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
