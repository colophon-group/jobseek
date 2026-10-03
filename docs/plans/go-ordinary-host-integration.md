# Native ordinary host integration delivery plan

Status: connected containment, scoped SQL quiescence, retained initial phase journal and selected Redis connection implemented; full host admission and production ownership change remain unproven.
Updated: 2026-10-03. Continue the full goal from
[the migration plan](../27-go-lightpanda-continuation-plan.md) and
[the source-bound checkpoint](../30-native-ordinary-authority-checkpoint-2026-09-30.md).
[ADR006](../adr/006-crawler-deploy-quiescence-and-rollback.md) remains authoritative.

## Starting evidence and gaps

Source `097027ac43481456efe810b683858fd66970060a` passes actual prior-source
ordinary execution at restored R and native/legacy root producer/finalizer
recovery on AMD64 and ARM64 (Linux run 37018098304). Its checked merge parents
and each prior binary hash are independently verified. Source `86cfb44f3` adds
separate actual prior image jobs using the unchanged pinned historical Dockerfile;
Source `eb804cf56` completes both architecture image jobs (37021649337), with
independently verified installed bytes/assets and source/image identity. Fixture labels and startup reference rows remain
synthetic. Neither test establishes a real production release generation.

The historical native image source `3cccd9f` was never selected as production's
ordinary owner. The last recorded production runtime `b75ccb9456bf29c9477f9747c0c2cc3908ad79bb`
lacks `ordinary_ownership.py` and `joint_ownership.py`. Therefore its actual
legacy worker must be exercised separately after legacy restoration. Its lack of
joint readers makes host exclusion, disabled automatic restarts and a completed
restoration mandatory; a current Python reader cannot stand in for that old image.
Obtain fresh production release/census evidence before selecting a rollout.

`apps/crawler/scripts/lightpanda-b0-cutover.sh` stops a B0-specific service list
that omits the exporter. It cannot provide the full joint cold attestation.
`apps/crawler/deploy.sh` intentionally refuses ordinary deploy while the active
B0 receipt exists. Keep that guard until a supported joint path is implemented,
verified and deployed. Do not route around it with a partial restart or receipt
removal.

## Ordered delivery slices

| Slice | Concrete result | Evidence required to advance |
| --- | --- | --- |
| Prior runtimes | Actual immutable historical native image and actual selected legacy image consume their final restored authority | Independent image IDs, source/CA/binary/assets, real RDB reload, policy/fence/queue/health/drain; synthetic fixture scope explicit |
| Release evidence | Native verifier and protected host request bind active, incoming and rollback release generations | Existing format-v3 Compose/env/success/data/runtime contracts and any transitive legacy bridge; exact deploy-spec presence/archive and images; negative drift/symlink/extra-file cases |
| Host exclusion | Supported coordinator holds the shared mutation lock and independently stops every writer plus exporter | Actual Compose service/container/restart identities, no running one-offs, no unaccounted writer, SQL lease/barrier checks; all observed without disclosing credentials |
| Durable coordinator | Fixed commands drive native CLI primitives and preserve exact outputs across crashes | Fsync before effects, exclusive producer lifecycle, canonical requests/receipts, phase-specific recovery and containment tests with real Linux executables |
| Full reversal | Original source E is retired once and the independently verified prior runtime returns at reserved R | Full spec/env/data/image restoration, B0/native or legacy authority, unchanged retired plan, no R+1, full-stack readiness and naturally due work |
| Production selection | Exact approved source/image generation is merged and deployed through supported workflows | Required CI and actual green Crawler Deploy Gate, no hold, fresh exact head/base/draft/merge checks, complete host receipts and rollback evidence |

Implement these as connected changes in the migration worktree; split a new
independent task into an isolated worktree. Preserve running evidence jobs;
new sources need their own admission. Do not repeatedly dispatch unchanged runs.

## Native file-verification primitive

The installed worker now provides `--verify-release-files`; its canonical result
binds the compiled source and credential-free `jobseek.crawler-release-files/v1`
evidence. Explicit generation directory/owner inputs are required; an optional
file-evidence digest binds a retry. The native library verifies generic v3,
initial format-1/2 bridges and transitive bridge attachments, exact CSV trees,
identity/hash pairs, override presence and supported bootstrap residue. Go 1.26
real-file and executable tests pass; offline Python is retained as a test oracle.
Both-architecture Linux/installed file verification is independently verified
at ad39 runs 37033316023/37033313737; fresh sources need their own checks.

This primitive reports only `files_verified`. The protected coordinator must
independently authenticate selected generations and observe cleared-environment
Compose/image resolution, exact deploy specs and complete writer quiescence
before constructing release/cold authority. Keep the shared mutation lock across
file verification and selection; the primitive reobserves every hashed file but
cannot substitute for the lock or a complete host receipt. Existing production
consumers still use their supported verification path.

The native `releaseevidence.ObserveImages` library now binds an explicitly
expected file-evidence hash to fixed, read-only `/usr/bin/docker` observations.
It clears caller Docker/Compose/interpolation variables, selects the protected
env and exact verified Compose/override paths, rejects builds/mutable images,
and requires every resolved repository digest to match a locally installed Linux
image ID on the requested architecture. Crawler/browser repository references
must match the verified env identities. Duplicate JSON keys, missing images,
platform drift and observation/file readback drift refuse. Output includes only
project/service/image identities and evidence hashes; raw Compose and inspect
JSON can contain secrets and stay in memory. It performs no pull or mutation.

Go 1.26 race tests and both Linux integration-tag vet checks pass. The existing
disposable Linux CI harness now requires an actual cleared-environment Compose
and image-inspect fixture using its already-pulled public PostgreSQL image; fresh
execution is now verified at ad39 run 37033316023 on both architectures. Initial
run 37032223264 rejected its first Compose
observation on both architectures after the database/queue suite passes. The
observer now uses HOME=/ and an isolated fixed Docker config path, with separate
command/JSON refusal labels; safe version-only probes test the root-home boundary.
Version-only probes show the old root-home command fails on both runners while
the corrected actual observation passes. That fixture has
synthetic crawler/browser identities
and proves the command boundary only. This library is not yet wired into a
production coordinator or a new installed-worker CLI command. It does not prove
image source/binary identity, selected generations/specs, mounts, numeric users,
complete writer exclusion or readiness. Those remain required before release
or cold admission; the caller must hold the shared host mutation lock.

## Actual installed host-request joins

The required installed-image harness now adds a positive request joining the
actual native image's independently retained binary/system-CA/34 assets to a
scoped, never-started service container and three distinct requested generation
directories. A disposable loopback registry provides a real immutable repository
digest without publishing an external release; the raw manifest is retained and
its content hash/config image ID are independently checked. Source, image,
service, missing/unscoped container, binary, CA and asset/membership substitutions
must refuse before retaining unverified intent/archive, with the context alive.

Local Linux compilation and workflow/repository checks pass. Fresh actual
AMD64/ARM64 execution and independent artifacts remain required. This replaces
no selection/provenance gate: generation roles/labels are synthetic and do not
authenticate production builds or selected pointers. Complete effective
permissions/process/dependency/mount/network/security/resource fidelity and all
subsequent exclusion/SQL/cold/readiness/rollout gates remain open. See
[the source-scoped join fixture](../evidence/go-ordinary-native-host-installed-join-2026-10-02.json).

## Native deployment-spec capture and retention

`--capture-deploy-specs` now provides native capture/retention of the deployed
ADR006 nine-spec archive. Explicit active generation/owner/file-evidence hash,
deployment directory and private archive path are required; optional archive and
capture hashes bind a retry. The canonical receipt binds the compiled source and
file-evidence hash. The primitive captures exact presence/absence and modes,
substitutes verified committed Compose for mutable live Compose, and rejects
closed-set/USTAR/hash/mode/path/framing drift. It retains one immutable mode-0600
archive under a private directory with file fsync, exclusive hard-link publication
and directory fsync, including exact retry. Archive destinations within the
committed generation, including aliases, refuse before publication.

Go 1.26 real-file races and actual executable tests pass. Actual deployed Python
writer/extractor code is used only as an isolated offline oracle; the installed
operation executes no Python, database, Redis or Docker command. Installed run
37037813202 at `b852b59bd4c946e32c593358056e38dd0d9334c6` independently verifies
all six actual executable fixtures on both architectures, plus source/CA and all
34 assets. This primitive
requires the caller's shared host mutation lock and reports only
`spec_archive_retained`. The host coordinator still must independently bind and
fsync intent/receipt before incoming spec selection, implement phased restoration
and final absent-file removal after bridge/data verification, and prove recovery
at real process-crash and readiness seams. No live specs are selected or restored
by the primitive.

## Native container inventory and Docker state predicate

`ObserveContainers` now reads the entire deployment daemon, including stopped
containers and foreign-project one-offs. Fixed cleared-environment commands,
bounded sorted inspection batches, duplicate-safe JSON, complete process/restart
fields and full readback bind a credential-free inventory. Configuration and
mount contents remain private; their hashes bind the observations.

`RequireColdContainers` joins exact project/service/local image IDs to explicit
release image evidence. All services are writers by default, including exporter
and new consumers. Writers, all one-offs and unaccounted global containers require
stopped flags/PID and restart=no/zero retries. Known-project unbound services or
images refuse. Only matched, explicitly non-one-off Redis/Postgres/Alloy daemon
commands may remain live; complete runtime/config/mount/user fidelity is a separate
mandatory check. Staged old/new image bindings share one host architecture.

Local Go races and both Linux integration-tag vet checks pass. The existing
Actions harness now requires actual whole-daemon observation of a synthetic
exporter before/after start, stop and restart-policy removal. Its independent live
PostgreSQL prevents a cold receipt. Fresh execution is required; this fixture
does not prove positive complete-host exclusion. The library is not yet a host
coordinator consumer. SQL barriers/leases, source/binary/mount/users, lock-spanning
reobservation, durable intent/receipt and readiness must still be implemented and
independently proved before complete admission.

## Release-bound execution and mount settings

`RequireContainerExecution` now compares known-project regular containers with
the reobserved Compose settings and installed image defaults: exact command and
entrypoint inheritance/clearing, complete configured environment, configured
numeric UID/GID, working directory, bind sources/access/propagation, named local
volumes, image-declared anonymous volumes and tmpfs options. Missing, duplicate,
extra or substituted mounts, unresolved/duplicate environment values, unknown
service/image bindings and active/restartable one-offs refuse. Unsupported mount
providers/options refuse until separately verified. Foreign identities remain
counted; the complete cold-state predicate still gates them independently.

The observer's optional protected `ProjectDirectory` preserves the actual
deployment base separately from verified generation Compose/env/override bytes.
Relative mounts must not resolve accidentally inside a rollback snapshot.
Directory mode/inode readback and a credential-free base digest bind this context.
Inventory v2 binds the full mount set in unique-destination order, preserving all
fields and exact JSON numbers. Older Engine mount-array permutations are not
identity changes; content/access/driver/unknown-field drift still refuses. Actual
fccd Linux readback failed on both architectures; the unordered two-mount issue
is reproduced locally, and fresh exact-source execution must verify the fix.
The source-scoped [mount evidence](../evidence/go-ordinary-native-canonical-container-mounts-2026-10-02.json)
retains both the failure and local checks. Do not waive full readback or admission.
The existing disposable Actions fixture now requires actual execution settings
and relative-bind/user/environment/command/access drift rejection; fresh actual
execution is required. Local Go races and both modules' Linux vet pass.

The receipt reports `runtime_admission:false`. Effective process UID/GID,
installed image/source/binary provenance, mounted-content fidelity and complete
namespace/network/security settings remain mandatory independent checks. This
settings verifier does not provide all of those, a host coordinator, SQL barriers,
complete writer exclusion or readiness. Continue these gates before any supported
cutover/reversal; do not promote a settings hash into full release admission.

## Retained-manifest installed bytes

`DecodeInstalledExpectation` validates a canonical digest-bound host expectation
for exact image ID/platform/runtime kind and binary/system-CA/assets, plus a
complete source tree, installed Python 3.13 wheel package and CLI entrypoint for
legacy Python. The real wheel is observed separately from repository fallback.
The expected digest and source/image
association must originate in independently authenticated build evidence and
protected retained host intent; this decoder does not establish that trust root.

`ObserveInstalledContainerFiles` uses cleared-environment fixed image-inspect and
container-cp commands for an already-existing exact container. It hashes and
discards two complete streamed archives, verifies exact file membership and
protected regular members, and rechecks the complete daemon inventory. Links,
devices, ambiguous/extra/missing paths, unsupported metadata, unsafe modes,
malformed framing and content/image/platform/readback drift refuse. It creates,
executes, removes and extracts nothing. Raw bytes/paths remain private.

The receipt reports `runtime_admission:false`. Its declared file hashes are not
source authentication, effective process identity, mounted-data provenance or
full root filesystem/security/readiness proof. Actual native observation is now
required in both existing installed-image jobs; its additional artifact must be
independently verified. The historical legacy-image job now requires the same
observer against its unchanged pinned image, with interpreter/system-CA/CLI/
source/package/assets and independent declared drift refusal. Fresh actual
execution is required, and historical rebuild coverage does not replace an
authenticated selected production image. Python dependency/link/import fidelity
also remains separate. Continue the complete host gates below.
See [the installed-byte contract](../evidence/go-ordinary-native-installed-file-observation-2026-10-02.json).

## Selected active files

`ObserveSelectedActiveFiles` now binds the explicitly requested active generation
to the deployed fixed pointer/root and regular live success marker. Canonical
physical trusted-owned protected directories, the exact absolute flat-generation
target, complete file evidence and matching marker bytes are required. Anchored
reads and full path/pointer/marker inode, ownership, mode, target and content
readback refuse aliases, unselected generations and same-byte replacements.
The connected preflight observes and reobserves this selection under the shared
lock and fsyncs the selected identity into intent and receipt.

Local Go1.26 races include the actual deployed file-only loader as an offline
oracle and substitution at both observation seams. Linux vet passes; fresh
actual installed selected-command/three selection-fault execution is required
on both architectures. This proves the selected active file boundary only.
Production build/image provenance, incoming/rollback selection, effective runtime
fidelity and every exclusion/SQL/cold/readiness/rollout gate remain mandatory.
See [the source-scoped selection evidence](../evidence/go-ordinary-native-selected-active-files-2026-10-02.json).

## Protected host envelope

The new installed `--host-preflight` entrypoint now connects the existing file/
image/inventory/declared-execution libraries and optional installed-file requests
under the shared mutation lock. It binds exactly requested active/incoming/
rollback roles, rejects unattached installed source/service/container identities,
retains immutable fsynced intent before the exact spec archive, and retains a
content-addressed receipt before returning. Retry reobserves the host; changed
request/spec bytes cannot replace rollback intent. Private ownership/mode checks,
lock/state inode readback and exact interrupted hard-link recovery are enforced.
No Python, Docker mutation, SQL/Redis primitive, spec selection or restart runs.

Its receipts report `runtime_admission:false`; requested role labels and hashes
are not authenticated build or selected-pointer evidence. An empty installed
request set is an explicit early-preflight scope, never full provenance. Local
native races include actual SIGKILL/lock/retention refusal cases; fresh installed
Linux command verification is now required on both architectures with synthetic
generations and a public infrastructure image. Actual installed-file joins and
selected production generation scope still need their own proof.

Continue the connected coordinator beyond this preflight: authenticated selection,
complete runtime fidelity, actual global/host/maintenance writer containment and
SQL barriers, durable fixed native forward/reversal phases, staged spec restore,
real complete host crash recovery/readiness and supported cold admission. Keep
the B0 guard and ADR006 intact; no preflight receipt can start writers.
See [the native host preflight evidence](../evidence/go-ordinary-native-host-preflight-2026-10-02.json).

The deployed coordinator must run under the existing operator deployment identity,
with the shared `/run/lock/jobseek-crawler-mutation.lock`. Agents never receive the
Docker socket. The wrapper observes Docker itself through fixed, bounded commands;
file contents, labels and probe output are data, never commands or environment to
source. An operator-owned regular file carries canonical intent and explicit hashes.
An immutable release digest must represent verified evidence, not caller-provided
labels or a convenient hash of an unverified directory.

Before any reserve, publication, producer reset, migration, sync or reversal:

1. Verify the exact active format-v3 generation and independently stage incoming
   and rollback generations. Bind source revisions, immutable image digests,
   Compose and env bytes, success markers, runtime contracts, exact CSV manifests
   and any legacy bridge transitively. Keep credentials in protected files.
2. Archive the exact active deploy-spec set with present/absent entries, arm the
   rollback journal and fsync its request and parent directory before selecting
   incoming specs. Preserve the verifier needed to restore a bridged generation.
3. Disable restart policies for all candidate/old writer services, then stop all
   ordinary/browser workers, native ordinary workers, B0 claimant/executor/producer,
   drain and exporter. Include any maintenance/sync one-offs. Check actual Compose
   service membership and each immutable container identity, not only process names.
4. Refuse unaccounted running one-offs or writers. Independently prove stopped
   containers and applicable database lease/barrier state. Bind the resulting
   canonical cold attestation to the exact release set and intent.
5. Keep the lane contained until retained authority and the complete selected
   generation agree. Failure to verify anything preserves stopped services and
   recovery evidence. Read-only historical inspection must remain available.

Compose resolution must use the selected protected env and immutable snapshot
set under a cleared process environment. Do not print resolved Compose or full
environment output: it may contain credentials. Reobserve image identities,
source, mounts, numeric users, restart policies and health before admission.

## Forward and reversal state machine

A wrapper checkpoint may report completion only after durably retaining and
validating the matching native CLI result. It must never choose the latest plan,
read the allocator as an implicit epoch, or infer success from a running process.

| Host phase | Native primitive / required evidence | Crash recovery |
| --- | --- | --- |
| Quiesced and bound | Verified releases/spec archive/all-writer attestation; staged ordinary plan and exact B0 target | Reobserve identical evidence under the lock; drift refuses |
| Intent retained | `--cold-begin` with canonical transition hashes | Inspect exact intent; no new transition ID |
| E reserved | `--cold-reserve`, immutable source E and ordinary plan | Reuse exact reservation; a nontransactional sequence gap is not a grant |
| B0 forward prepared/applied | `--cold-b0-forward-plan/retain/apply/inspect`, actual producer manifest and acknowledged SAVE/readback | Recover retained phase without duplicate transfer or queue loss |
| Ordinary published/active | `--cold-forward-prepare/publish/activate`, immutable approval and B0 completion | Recover exact routing bytes and SQL phase; no writer starts before joint active authority |
| Candidate release selected | Complete committed release generation, authenticated receipts and all identity readiness | Start the full stack once; failed readiness returns to contained reversal |
| Reversal retained / R reserved | `--cold-reversal-begin/reserve/inspect` with exact original E/plan and verified rollback release | Reuse R; never allocate R+1 as a retry |
| Source queues/fences restored | `--cold-b0-rollback-plan/retain/restore/inspect` | Reobserve full conserved source manifest and retired E; keep all writers stopped |
| Ordinary restoration retained | `--cold-ordinary-rollback-plan/retain/inspect`, explicit legacy or fresh prior-source native decision | No ordinary publication or B0 grant yet |
| Prior B0 reactivated | Protected initializer plus `--cold-b0-reactivation-plan/retain/apply/inspect` | Clear/reinitialize only with the exact decision and lifecycle lock; recover SAVE and completion |
| Ordinary finalization | `--cold-ordinary-finalization-plan/retain/prepare/publish/complete/inspect` | Recover acknowledged SAVE and atomic SQL closure; fresh native R only, or explicit legacy witness |
| Prior release selected and ready | Full prior spec/env/data/image selection, actual prior runtime, all service readiness and naturally due work | Retain failure containment if restore/readiness cannot be proven; historical inspect is not a new grant |

The coordinator must persist CLI results atomically with fsync and strict
source/intent/E/plan/reversal/R binding. Produce a canonical completion receipt
for consumers; do not scrape ad hoc log strings. Mount the exact selected CSV
tree read-only for required resync in its corresponding immutable image. Restore
previously absent specs to absence only after required bridge verification and
rollback finish, following ADR006.

## Verification and rollout pacing

First prove non-mutating release verification and host exclusion against actual
Linux fixture files and controlled Compose processes. Then exercise forward,
reversal and exact recovery with actual UID-10001 producer and coordinator
executables, real PostgreSQL and RDB reloads. Kill processes at host journal/spec
selection, reservation, producer reset, SAVE, publication SQL, atomic completion,
release-pointer selection and readiness seams. Check all task families, source
queues, due times, failure budgets, canonical content, old-plan retirement and
recovery after source/asset/receipt drift. A mock-only test does not admit the
operational contract.

After passing exact-source checks, review and merge the preparatory stack with
fresh merge authority and required gates. Obtain current release/image/data and
board/profile census, preflight disk/connection/CPU/RAM capacity, then use the
supported deploy/coordinator path for a bounded first ordinary cohort. Exercise
full cold reversal before expanding ownership. Reconfirm every enabled board is
owned exactly once and naturally due work settles at the correct epoch.

Continue by effective profile, preserving a frozen inventory and concrete oracle
samples. Replace each remaining monitor, detail/browser path and runtime consumer
with Go plus self-hosted Lightpanda or verified Go HTTP/API. Measure comparable
whole-service CPU/RAM, density and attributable cost, including sidecars,
references, queues, database/export/drain, failed work and redirects. The current
generated B0 benchmark alone cannot satisfy that acceptance.

Keep the full migration goal active until all enabled coverage, canonical and
publisher-policy parity, freshness/conservation, production deployment and the
schedule-dependent rollback window are proven. Then remove production Python
execution, Playwright, Chromium and runtime-only assets and remeasure the final
service. Preserve useful isolated offline Python tooling and every enabled board.
Use Hetzner Codex scheduling if recurring work is needed; this plan creates no
new schedules or notification routes.

## Connected writer containment phase

`--host-contain` requires explicit `ORDINARY_GO_WORKER_MODE=host-contain`, the
compiled coordinator source, protected request directory/SHA and exact
`ORDINARY_HOST_PREFLIGHT_INTENT_SHA256`. It reacquires the fixed mutation lock,
verifies the original immutable preflight intent and private spec archive,
reobserves active selection, all three file/image roles, installed-file joins,
complete inventory and declared execution, and retains `containment-intent.json`
before effects. This phase permits only fixed restart disabling and stopping
exact retained regular-service container IDs. Every such writer needs joined
installed-file coverage; exporter and new services are writers by default.

The plan retains the full prior daemon identity set, original restart policy,
config/mount hashes and a complete host-config hash excluding only RestartPolicy.
Foreign live/restartable objects and unknown project services/images refuse;
maintenance one-offs must already be cold and never acquire stop authority.
Matched Redis/Postgres/Alloy daemon roles remain live. Repeated pre-effect guards
validate the lock, retained bytes and fresh selected/file/image/spec/installed
observations. New/missing/replaced container IDs or unrelated configuration drift
refuse. Linux CLI children die with the coordinator; already accepted daemon
requests may finish and require reconciliation before any future restoration.

`restarts-disabled.json` is fsynced only after all target restart policies are
observed no/zero, before the fixed stop command. A retry with this barrier refuses
a regained restart policy. A partial pre-barrier restart update resumes only
the exact original IDs; no failure restarts a service. The post-effect full daemon
inventory must pass `RequireColdContainers` before an immutable content-addressed
receipt and result report `docker_writers_contained`. Both
`runtime_admission` and `sql_barriers_observed` remain false.

Local race tests, Linux compilation and required repository checks pass. The
installed-image CI harness owns two sleeping stand-in service containers with
restart=always, including exporter, plus a matched live PostgreSQL service. It
requires real installed CLI preflight/containment, uncovered-exporter refusal,
retained-file SIGKILL recovery, exact retry, rollback-archive substitution refusal
and complete actual Docker readback on both architectures. It proves the stop
operation, not a real worker workload or production host/SQL admission. Fresh
source-scoped artifacts must be independently verified. Next join this phase to
actual SQL barriers/leases and the full cold state machine, maintaining exclusion
across all phases, then prove restoration and readiness before production use.

## Connected SQL quiescence scope

`--host-quiesce` requires its own explicit mode and the same compiled source,
protected request and retained preflight intent. It verifies and parses the exact
selected generation's protected `LOCAL_DATABASE_URL` before new containment effects;
no shell, caller PG settings, service/password/client-key file or ambient database
URL supplies credentials. Credentials remain in memory and never appear in receipts
or diagnostic output. Exact regular writer IDs are contained through the existing
retained restart/stop sequence under the shared mutation lock.

The private PostgreSQL backend then holds the ordinary, routing and CDC exclusive
session barriers, observes all legacy board/posting lease columns with the database
clock, and refuses any live lease without clearing it. An unavailable barrier causes
the complete acquired prefix to be released before retry, avoiding a wait with
partial exclusion. All cold SQL transactions in this callback use the same backend
and commit independently; session locks survive commits/rollbacks. Closing that
private connection on every exit releases its locks rather than returning a locked
session to the pool. The callback is bounded; backend PID, exact lock set, idle state
and zero leases are reobserved.

Fresh cold Docker predicates before and after immutable quiescence receipt fsync
run while both the host lock and SQL session are held. The returned receipt records
that overlap; the CLI then closes its scope. It cannot grant admission or authority
to a later separate command. Connect the complete cold state machine inside this
live callback, including Redis lease/producer lifecycle and all original intent,
epoch, staged restore, data/spec absence, full-stack readiness and recovery gates.
Nonparticipating SQL clients and host timers require their own verified exclusion.

Local actual fully migrated PostgreSQL tests verify durable commit/sequence rollback,
shared writer exclusion, board/posting lease refusal and lock release. The disposable
installed harness requires actual selected-database parsing despite poisoned caller
PG settings, observed shared-writer waiting, SIGKILL backend lock release and exact
retained intent retry. Both writer processes remain sleeping fixture stand-ins.
The PostgreSQL fixture declares GitHub's injected GITHUB_ACTIONS/CI environment
rather than weakening the execution predicate. Sources 7e/3bc failed strict
preflight before stops; fresh installed artifacts must pass independent verification.
See [portable SQL evidence](../evidence/go-ordinary-native-host-quiescence-2026-10-03.json).

## Cold driver inside the live host session

Use `WithHostQuiescence` to execute the driver before closing the host/SQL scope.
The callback receives its exact live context and selected pool. Cold admin uses
`RunColdAdminInHostScope`, which checks source/context/pool identity before and
after execution and borrows its Redis client. Every branch, including B0
reactivation and ordinary finalization, uses this connection path. Separate child
cold commands or a new database pool would contend with the held barriers and
cannot replace this driver. Standalone historical commands retain their own
connection lifecycle; historical inspection remains available without live authority.

Actual private PostgreSQL/Redis tests capture a B0 target, retain the transition
intent, reserve an epoch, inspect the committed phase and repeat the exact
reservation while all three writer barriers remain excluded. Independent observers
see the pending intent before reservation; complete Redis/canonical snapshots stay
unchanged and no extra epoch is consumed. Different sources/pools refuse and borrowed
resources remain usable after the scope. The actual existing cold-publication
SIGKILL executable regression also passes with the unified connection helper.

The disposable installed test now additionally runs the library callback against
real Linux Docker/PostgreSQL. It must observe the actual shared host flock and all
SQL barriers held inside both successful and rejected callbacks, backend release,
and exact cold containment after failure. This tests the in-process host boundary;
it does not prove a complete installed CLI phase driver, real worker readiness or
production authentication. Fresh exact-source artifacts must pass before claiming
this callback contract is verified.

Next define and validate the protected canonical host phase request, derive all
release/intent/E/plan/reversal/R bindings from retained evidence, and privately fsync
each exact result before its next effect. Bind Redis to verified effective selected
execution instead of caller URL fields. Drive the entire existing native forward/
reversal/finalization sequence inside the callback, preserve producer lifecycle
exclusion, and stage present-spec/env/data restoration before final absence. Then
exercise full host SIGKILL/readiness/naturally due work on both architectures and
finish the profile/consumer/parity/resources/cost/production gates in the full plan.

The current quiescence guard deliberately requires the original selected pointer,
live marker, request, files and spec archive throughout this scope. A complete host
coordinator must authorize each intentional spec/selection change from exact durable
phase evidence; it cannot weaken that guard or treat changed files as unexplained
success. Keep the outer host lock across selection, SQL-scope release, full-stack
startup and readiness. Running workers need SQL barriers released before startup,
so startup/readiness belongs after the cold SQL callback under the same outer host
transaction. The current one-shot quiescence wrapper does not implement that handoff.


## Protected first cold phase journal

`WithHostQuiescence` now privately constructs an opaque phase scope from the
held host lock/store, complete cold container evidence, all three selected role
file-evidence hashes and exact live SQL binding. `InspectHostColdPhaseContext`
returns a past-observation shape for staging protected native intent bytes;
`RunHostColdPhase` requires the original live context/pool. A stable attestation
joins the selected role hashes, containment request, cold container hash, database
hash, exclusive barrier set and zero SQL leases, excluding the changing backend
PID. It does not authenticate arbitrary nonparticipating writers or Redis callers.

Stage exact canonical requests as `cold-request-<sha256>.json` and inputs as
`cold-input-<sha256>` under the existing owner-only 0700 request root; every file
is immutable 0600 with anchored no-symlink/single-link verification. The closed
request graph currently accepts only target -> begin -> reserve -> inspect.
Release/spec selection, publication shortcuts, shell commands, paths, arbitrary
environment maps and endpoint URLs are rejected. Begin joins the captured target
and the active/incoming/rollback/cold attestation fields before native intent.

The driver retains request, a unique successor claim and phase intent before
native effects. It then retains a typed completed or unresolved result, followed
by two immutable completion indexes, before permitting a successor. Raw errors
and credentials are never journalled. An unresolved result asserts no absence of
effects; a new explicit event must name it and preserve every native input to
retry. A completed exact request returns its retained historical result without
repeating native effects. Pending result hard-links recover only their exact
inode/bytes; a result without completed indexes cannot authorize a successor.
Retry ancestry is bounded to64 unresolved events and still joins the original
completed reservation when validating inspection.

Actual private PG/Redis tests verify these boundaries plus two test-binary kernel
SIGKILL seams. Their private flock and release/container hashes remain fixtures.
The disposable installed host callback must independently verify its new opaque
phase context against actual selected roles and cold containment, stable backend
retry and escaped-context refusal. The Linux worker workflow now prints individual
race-test results so the journal tests can be verified from exact-source logs.

The next phase graph must bind the effective selected Redis endpoint and complete
producer/lease exclusion, then wrap the entire existing forward/reversal pipeline
and every E/plan/reversal/R/result binding. Preserve the strict original selection
predicate until an exact durable next phase authorizes its deliberate change.
Extend the outer host transaction across cold SQL scope release and whole-stack
startup/readiness; the one-shot wrapper still ends its host lock on return.
See [portable phase evidence](../evidence/go-ordinary-native-host-cold-phase-journal-2026-10-03.json).

## Selected Redis connection and journal binding

The production host callback supplies the Redis resolver from freshly guarded
selected-role file/image/execution/inventory evidence. `WithSelectedHostColdRedis`
opens its own native client inside the live host/SQL callback, retains a protected
hash-only receipt and closes the client on every exit. Redis URLs come from
resolved Compose and image defaults, including stopped matched consumers;
`environment.env` need not contain `REDIS_URL`. All declared consumers must agree
on a closed, explicit host-network loopback route and database. The actual official
Redis daemon's command, image and host network must match, and Linux `/proc`
must join its PID to the unique listening inode. No caller URL/client fallback or
arbitrary kernel observation root is accepted by the production API.

Version-2 requests bind endpoint and server-incarnation hashes. A changed PID,
socket, selected configuration or incarnation invalidates the old authority;
recovery must not silently adopt it. The journal still accepts only target,
begin, reserve and inspect. Existing source-bound version-1 evidence remains
historical; the candidate intentionally refuses implicit request upgrades.

The disposable Linux harness must prove an actual selected consumer/daemon/socket
join, read-only incarnation, ignored caller environment, wrong-PID refusal and
stopped/restarted stale authority on both architectures. Its consumer sleeps;
this is endpoint evidence, not producer/lease exclusion. The installed callback
fixture separately refuses a missing selected Redis endpoint while retaining
host/SQL exclusion. Positive full host callback -> selected Redis -> retained
journal effects, complete forward/reversal graph, phase-authenticated expected
host changes, outer lock through SQL release/startup/readiness and actual installed
full-stack recovery remain the next connected delivery work.
