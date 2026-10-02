# Native ordinary host integration delivery plan

Status: proposed implementation; no host admission or production ownership change.
Updated: 2026-10-02. Continue the full goal from
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
complete source tree for legacy Python. The expected digest and source/image
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
independently verified. Legacy-kind unit coverage does not replace an actual
selected legacy runtime observation. Continue the complete host gates below.
See [the installed-byte contract](../evidence/go-ordinary-native-installed-file-observation-2026-10-02.json).

## Protected host envelope

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
