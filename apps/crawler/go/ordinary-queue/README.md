# Native ordinary worker authority foundation

This unselected library extends the existing Redis queue and PostgreSQL write
contracts. It adds no executable, production profile selection or separate ready
queue. Python ordinary workers remain the production owners until supported
quiesced cutover installs exclusive native selection and complete processing.

## Attempt identity and existing queue contracts

`Claim` retains the tokenless legacy ABI. `ClaimFenced` allocates a private random
128-bit attempt token, uses Redis TIME, and returns the stored lease deadline.
The same token must match an unexpired lease in heartbeat and settlement. The
`inflight_tokens:<wtype>` index extends existing lease state; ready queues,
domain fairness and source identity retain their existing representations.
Legacy heartbeat/completion/reschedule cannot modify a tokenized attempt.

The client disables mutation retries, bounds I/O, and retains the claimed task
when configuration loading fails. Callers must handle a partial task/error pair
without accidentally abandoning an inflight claim. Descriptor configuration is
a detached snapshot; authority descriptors do not expose the claim token.
Errors omit connection strings and upstream configuration content.

## Native database authority

Migration `0035` retains a fence for a monitor inventory or a detail attempt in
`ordinary_worker_write_fence`. It records the existing global routing epoch,
board identity, attempt token, configuration digest, completion state and
canonical next deadline. The SQL embedded in Go must match the applied migration
byte for byte. Its trigger rejects persisted fences from retired epochs.

`OpenAuthority` verifies the schema and expected current epoch before claiming
work, with one owned PostgreSQL connection. The expected epoch must come from a
verified active ownership plan; reading and adopting the allocator's latest
value is insufficient. The library reuses the B0 routing sequence and retirement
barrier instead of introducing another allocator. Any supported B0 epoch
retirement also retires ordinary authority. Selection/startup/cutover must
coordinate these owners before native ordinary work is admitted.

Transactions take locks in this order: ordinary lease barrier, existing routing
epoch barrier, canonical board/detail rows, retained fence. Claim, heartbeat and
writes hold shared barriers; settlement and the production Go lease reaper take
the lease barrier exclusively. Epoch retirement takes the existing routing
barrier exclusively. This orders lease removal and epoch retirement before or
after the entire native transaction. All participating writers must acquire the
barriers before row locks; the fence trigger is an additional epoch check.

Native writes run inside a bounded transaction with fresh Redis configuration
and token checks before and after the callback. Canonical detail-to-board
mapping is locked and verified. Fetch/render/enrichment must run outside the
transaction. The callback owns the actual native canonical effects; this library
does not implement monitor inventory, enrichment, disappearance or failure
policy. Terminal writes retain the database-owned `next_check_at` or
`next_scrape_at` and return an opaque receipt. Settlement accepts only a matching
committed receipt whose deadline still equals the canonical database deadline.
A NULL detail deadline completes rather than reschedules the task.

After a committed transaction loses its acknowledgement, a subsequent attempt
can recover an unchanged future canonical deadline or NULL completion, including
across epoch retirement, without repeating effects. Changed configuration,
board mapping or deadline prevents receipt recovery. Nonterminal streamed writes
require native processing to preserve its own idempotence and completeness
contracts. Raw queue methods are not a PostgreSQL write grant.

The production Go reaper holds the database barrier through its Redis sweep.
Legacy direct Python reaping skips tokenized attempts while continuing legacy
work. Lua's optional `guarded` argument identifies the wrapper's sweep; it is not
a secret or security capability. Every native lease-ending path must participate
in the database barrier. Old deployed scripts unaware of tokens cannot safely
coexist with native selection.

## Verification and its limits

Migration `0036` retains immutable staged/active/retired ownership documents in
`ordinary_worker_ownership_plan`. The document binds the global epoch, exact
source revision, supported member identities and stable configuration hashes.
PostgreSQL checks the exact payload SHA256 and required envelope fields; Go
rejects duplicate keys, unknown fields, unsupported members and noncanonical
serialization. Only one plan may be active. Retired identities cannot be
reactivated or deleted. State transitions take the existing lease and epoch
barriers; activation requires the current allocated epoch. Ordinary readback
uses those barriers without a conflicting ownership row lock.

`StageGreenhouseOwnership` captures enabled active canonical boards and their
matching Redis settings as one staged document. Partial inventories roll back;
staging allocates no epoch, changes no queue and grants no authority.
`LoadActiveOwnership` requires an exact caller-attested plan/revision and the
already verified epoch. It never adopts the allocator's latest value.
Generic unbound Go claims, writes, heartbeat and settlement reject an active
plan before queue mutations or callbacks. These guards do not implement the
future bound native claimant or modify legacy Python claims.

There is no production activation endpoint in this library. Before activation,
the supported all-writer release must install atomic native/legacy selection,
exact startup identities, Redis projection integrity/loss protection and full
native processing. Direct SQL activation appears only in owned private tests.

Private Redis race tests cover both worker queues, attempt expiry/reclaim,
stale heartbeat/completion/reschedule, exact scheduling, deferred monitor
repair, duplicate representations, deadletters/orphans/guard cleanup, corrupt
index preflight and cancellation. Fixtures own private processes/directories and
never clear a shared Redis database.

Real migrated PostgreSQL/Redis tests cover monitor/detail transactions in both
queues, canonical posting/description/upload-state/board effects, exact
settlement, callback/cancellation rollback, configuration changes, delayed
activation after expiry, retirement ordered after commit, stale epochs rejected
by the schema, guarded reaper exclusion and commit-before-ack recovery across
retirement. The production Go `Sweep` wrapper has its own barrier regression.
Migration upgrade/downgrade/re-upgrade is checked against the actual schema.
Ownership tests additionally verify immutable transitions, single active
membership, stale epoch/source rejection, malformed SQL envelopes, rollback of
partial staging, unbound operation exclusion and transition/readback ordering.
These are authority fixtures, not extraction/enrichment parity or an actual
native ordinary process SIGKILL/cold-reversal proof.

The `Go ordinary queue contracts` workflow requires real private Redis and an
actually migrated PostgreSQL 17 fixture on Linux amd64 and arm64. Missing required
fixtures fail. Local authority race tests use PostgreSQL 18.6 and Redis 8.10.1.
A skip or an earlier revision's green run establishes no new-head proof. Record
exact-head CI results in the PR before considering selection.

## Continuation gates

The offline `InspectGreenhouseMonitor` boundary observes the standard explicit
token/skip profile on the existing board hash. It validates canonical board and
company IDs, provider URL, both browser flags, intervals and unchanged shared
throttle identity, rejecting unknown/filter/proxy/enrich/alternate-token settings.
Empty or null skip options have no extraction effect. It makes no request or
queue mutation and cannot establish enabled status, profile ownership or write
authority. Its effective digest excludes runtime lifecycle/learned-egress
observations; its exact snapshot digest still binds all of them. Native
processing must freshly validate and preserve their policy before effects.

`Authority.ObserveGreenhouseMonitor` joins that observation to the actual
enabled, active PostgreSQL board while holding the existing routing epoch
barrier and a shared canonical row lock. It compares company/source/interval/
throttle/browser/effective metadata settings to the current Redis hash and
returns no profile on a stale projection, unsupported state or retired epoch.
It neither claims work nor installs a fence. Suspect/gone/quarantined rows keep
their current owner until native lifecycle processing is proven. Actual ready
route membership, active-plan ownership and fresh canonical state still must be
checked at selection; this observation grants no later authority.

The durable document and exact active readback are prepared. Bind them to
exclusive native and legacy selection before claiming.
Eligibility must be checked atomically before a pop, with bounded progress past
unselected heads on mixed domains. Legacy claimants must exclude selected
profiles and both sides must fail closed when expected routing state is missing
or corrupt, including complete Redis loss. Neither a validated profile nor the
current generic `Authority.Claim` meets those ownership requirements. An active
document alone is insufficient to select a production owner.

1. Bind effective profile/domain eligibility to exclusive native ownership in
   existing claims. Preserve every unselected task, domain fairness, rate limits,
   deferred monitors, never-successful work and repair. Coordinate the current
   epoch with the supported all-writer cutover and reversal plan.
2. Connect the first proven native HTTP/API family to monitor and detail
   processing. Greenhouse is the leading census candidate, pending effective
   configuration validation. Preserve inventory completeness/truncation,
   disappearance, source identity, native enrichment, description dedup/R2,
   retry/circuit/publisher policy, disabled/deleted and never-rescrape semantics.
3. Prove actual process cancellation/crash, commit-before-ack recovery and
   supported cold reversal, then canonical output, freshness and queue
   conservation using exact immutable candidate images.
4. Merge/deploy only after required CI and the actual Crawler Deploy Gate pass
   with fresh head/base/hold/ownership checks. Expand profile coverage until every
   enabled board has a proven route and all ordinary scheduling/maintenance
   consumers run natively.
5. Compare whole-service CPU/RAM/density/attributable cost, pass the rollback
   window, and retire production Python, Playwright, Chromium and runtime-only
   assets. Preserve useful isolated offline Python tooling.

The full migration goal stays active through these gates. A deployed B0 cohort,
this authority library, or a selected first HTTP family is only a checkpoint.
