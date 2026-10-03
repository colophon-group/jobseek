# ADR 008: Durable web company references

Status: accepted for implementation under epic #10222; expansion and bridge are
compatible phases, and final foreign-key cutover has separate promotion gates.
Incident: #10214. Historical catalogue cutover: #6165 / PR #6255.

## Context

Removing company mirroring left Typesense discovery and web selection persistence
with different identity sets. Searches exposed canonical companies that had no
legacy web `company` row, while watchlists and stars required that row through a
foreign key. Read-source tests verified the catalogue change without testing
first-use persistence. One-record repairs masked a lifecycle defect. A retained
reference table needs a writer and a retention contract even when the full
catalogue no longer belongs in the web database.

## Ownership and identity

Crawler PostgreSQL allocates canonical company UUIDs. Typesense `company`
publishes those UUIDs and current display metadata. Search availability, names,
and slugs do not allocate identity. The web database owns selections and their
last-known display references in `company_reference`:

| Field | Contract |
| --- | --- |
| `id` | Canonical UUID primary key, with no random/default allocator |
| `name` | Nonblank text, at most 300 characters |
| `slug` | Nonblank text, at most 100 characters; mutable, nonunique |
| `icon` | Nullable text, at most 2048 characters |
| `source` | `legacy_seed` or `typesense` |
| `verified_at` | Last server verification instant; required for `typesense` |
| `created_at`, `updated_at` | Timezone-aware instants |

Bounds, forbidden controls and safe slug syntax match the public watchlist company normalizer, preventing persisted
references from silently disappearing during rendering. Existing legacy rows
keep exact UUID/name/slug/icon and UTC-interpreted timestamps. They use
`legacy_seed`, with no invented verification timestamp. Malformed metadata
aborts expansion with a count-only diagnostic; review and repair the source
before retry. No row is silently omitted.

Server-side materialization resolves missing IDs in bounded batches against the
canonical Typesense company collection, checks exact returned UUIDs and display
fields, and rejects partial results. Browser-supplied display fields are never
verification evidence. Authorize the user/target before external lookup; perform
lookup before taking transaction locks. Atomically persist verified references,
the compatibility rows while required, and selections. Upserts must tolerate
concurrent first use without downgrading verified provenance. Slug conflicts
must not overwrite another UUID, invent a new UUID, or merge companies.

Renames and slug reuse change display metadata, not UUID identity or membership.
An existing reference remains usable when Typesense is unavailable or no longer
publishes that company. Unknown IDs fail explicitly and leave the whole mutation
unchanged. Removing selections needs no catalogue lookup. Empty or partially
resolved scoped selections must never become an `anyCompany` search.

## Retention and deletion

Catalogue retirement does not delete references or user data. Final selection
foreign keys target `company_reference.id` with `ON DELETE RESTRICT`; ownership
foreign keys continue cascading when the user/watchlist is explicitly deleted.
Reference removal is not part of catalogue sync and is not exposed to browsers.
Unreferenced reference cleanup requires a separately reviewed retention policy;
this change retains them. `saved_job` remains independent and preserves its own
company/posting snapshots and application history.

Expansion intentionally leaves legacy selection FKs unchanged for compatibility.
The old `ON DELETE CASCADE` company relationship is removed only at final
cutover. During this window, operators must not delete legacy company rows.
The expansion alone does not resolve #10214 or satisfy final retention behavior.

## Compatible phases and rollback floor

1. Expand and seed under a write lock on legacy `company`; install its compatibility
   trigger in the same transaction. Late old-version inserts/updates then create
   or refresh only `legacy_seed` references. Existing verified metadata survives.
   Runtime writes and browser privileges are checked before bridge promotion.
2. Deploy a bridge that materializes minimal legacy rows and new references in the
   same transaction as new selections. Insert legacy rows before reference promotion
   in sorted UUID order, matching the legacy trigger lock order and avoiding
   company/reference lock inversion during old/new writer coexistence. Move durable selection reads and previews
   to references. Old application instances can still read their legacy rows.
3. Verify first-use persistence, exact membership preservation, authorization,
   concurrent additions, search outages, retirement, reload, sharing and handoff.
   Require real PostgreSQL plus authenticated browser/deployment canaries.
4. After all active writers/readers and rollback artifacts support references,
   switch watchlist/star FKs to restrictive reference FKs in a separately reviewed
   contract migration. Stop legacy writes only after the bridge rollback floor
   and canaries are proven. Preserve inactive legacy consumers until their own
   retirement audit; do not drop `company` in this epic as an unexamined shortcut.

Before contract, rollback to the compatible bridge retains both representations;
old versions can run during expansion but still have the original first-use
limitation. After stopping legacy writes, pre-bridge versions are unsupported:
rollback must use the compatible bridge or a forward repair. After FK cutover,
rollback never retargets FKs to incomplete catalogue mirrors or deletes selections.
Schema expansion remains in place during application rollback.

## Verification and follow-up

See [dependency inventory](../company-reference-dependencies.md) and
[rollout runbook](../runbooks/company-reference-rollout.md). CI owns a behavioral
boundary contract: a company visible in search but absent from the web database
can be persisted, rendered and removed through production services. Producer
changes must run this contract too. Deployment promotion requires an authenticated
mutation/reload canary and aggregate failure telemetry. Full catalogue parity is
unnecessary; missing persisted references and failed valid first-use mutations
are the actionable invariants.

Inactive mirror tables, Murmur compatibility and historical verifier fixtures
have explicit owners/dispositions in the inventory. This migration does not
activate company-request automation, signals, or notification delivery.
