# Supabase Free downgrade runbook

This runbook tracks the staged removal of Supabase's crawler-owned mirror while
preserving the small web-owned database. It deliberately excludes Murmur and
unimplemented Stripe/Resend surfaces.

## Migration baseline repair (#6181)

Production was audited read-only on 2026-08-03:

- PostgreSQL 17.6, 1,643,850,899 bytes total;
- 72 rows in `drizzle.__drizzle_migrations`, with 0079 as the latest recorded
  repository migration;
- 0080's experience-column rewrite absent (`integer` remains);
- 0081 partially present (interview CHECK present, private watchlist default
  absent);
- 0082's exact valid partial index present;
- 94 watchlists: 85 public and 9 private.

The superseded 0080-0082 files are removed from the active journal. Migration
`0083_reconcile_supabase_baseline` accepts only the audited 0079 ledger tip,
does not rewrite `job_posting`, repairs the small schema objects, and verifies
its own postconditions. Existing watchlist visibility is counted under a table
lock before and after the default change.

Apply only after the PR is merged to `main`:

1. Open **Actions → Web Database Migrations → Run workflow** on `main`.
2. Select `apply` and enter `APPLY-0083`.
3. Approve the reviewer-gated `production-migrations` environment.
4. Retain the preflight/postflight JSON from the job summary with the downgrade
   evidence.

The workflow uses only `DATABASE_URL_UNPOOLED`, serializes runs, reserves one
physical database session for its advisory lock, and refuses the Supabase
transaction-pooler port. The separate, non-approving
`production-migration-drift` environment exposes only a read-only role; its
daily run verifies that the exact reconciliation hash and schema postconditions
remain present. Do not reuse the general `production` environment: it has no
review gate and is also needed by unattended crawler maintenance.

The GitHub environments were provisioned on 2026-08-03 with protected-branch
deployment policies. `production-migrations` requires an owner review and holds
only `DATABASE_URL_UNPOOLED`. `production-migration-drift` holds only
`DATABASE_URL_READONLY`. The latter authenticates as
`jobseek_migration_auditor`, whose role defaults every transaction to read-only,
has no superuser/database/role/replication/RLS-bypass attributes, cannot create
in `public`, and can select the migration ledger and audited application tables.

## Saved-job cutover order

Do not deploy a schema+app+constraint-drop migration as one release. The safe
sequence is:

1. add the original salary fields to Typesense, deploy the crawler schema, run
   a full posting backfill, and prove every saved posting with
   `pnpm db:verify-saved-job-typesense` using the production server read key;
2. expand migration: nullable snapshot columns, backfill, replace the
   cascading posting FK with `ON DELETE RESTRICT`, validate the temporary
   completeness CHECK, and protect old-app inserts;
3. app release: write complete snapshots, read snapshot-first, and reject
   incomplete new saves while allowing existing saves to be removed during a
   Typesense outage;
4. contract migration: lock `company`, `job_posting`, then `saved_job`; catch
   up only missing required values; require the seven identity fields; retain
   a permanent validated nonblank-text CHECK; then remove the temporary CHECK,
   compatibility trigger/function, and posting foreign key last;
5. prove encrypted backup/restore, stop mirror writes, and only then drop the
   crawler-owned tables.

Salary and icon fields may remain nullable. Posting title, source URL,
first-seen timestamp, active state, and company identity/name/slug must be
complete before the contract phase.

The 0085 preflight verifier checks the exact 0084 ledger/catalog and reads the
mirror only to prove catch-up sources. Its postflight and scheduled drift modes
query only durable saved-job/catalog state, so verification remains usable
after `job_posting` is removed.

Production source evidence collected read-only on 2026-08-03 shows 262 saved
jobs and zero missing required source fields. Salary is absent for 229 rows and
therefore remains intentionally nullable. Migration 0084 installs the old-app
compatibility trigger before its backfill and fails unless every required
snapshot is complete and the outbound posting FK still exists.
