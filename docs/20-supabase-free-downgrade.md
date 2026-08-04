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

## Apify vendor retirement (#6248)

Meta posting ingestion belongs to the crawler's `meta-careers` sitemap and
JSON-LD board. The former web-only Apify endpoint and importer are retired; the
web deployment has no fallback path that writes crawler postings. The retired
vendor-backed company-discovery tools and their MCP instructions are also
removed, along with the one-off bulk company-request importer. This does not
affect the tracked Apify company or its generic Ashby career board. A
TypeScript-symbol-aware source test covers deployable TS/JS modules in `app`,
`src`, an optional root `pages` tree, and explicit Next root runtime entries
such as `proxy`, `middleware`, instrumentation, and MDX components. It rejects
Drizzle mutations through relative imports, namespaces, aliases, re-exports,
and local indirection, plus raw SQL and Supabase `job_posting` mutations.

The merged native-crawler cutover in #2361 recorded 534 sitemap URLs in 3.0
seconds and 10/10 successful stratified job-page samples in 1.8 seconds. The
same crawler pipeline persists local PostgreSQL truth and exports it to
Typesense. A repository-level static guard rejects the retired vendor's API,
credential, client-package, and MCP discovery markers in executable,
configuration, and dependency surfaces. Secret-store cleanup remains an
operator follow-up after the deployed Meta freshness canary and is not
performed by the code change.

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

## Crawler runtime cutover (#6249)

The crawler deployment boundary is staged without changing protected secret
contracts. `.github/workflows/deploy-crawler-browser.yml` maps the existing
provider-neutral `DATABASE_URL_UNPOOLED` secret into the remote process only as
`WEB_DATABASE_URL`. `deploy.sh` writes that separately named value for explicit
watchlist sync/count-refresh jobs and omits `DATABASE_URL` entirely.
Deployment files arrive in `/home/deploy/incoming`; the active env and complete
Compose deployment spec are snapshotted before activation. A failed rollout
restores both before restarting the previous image, preserving the previous
credential semantics rather than combining an old image with a new Compose
allowlist. Rollback starts Compose with an empty process environment plus the
restored env file, so the failed SSH process's `CRAWLER_IMAGE_TAG` and other
inputs cannot override the old contract.

Before the first rollout of this mechanism, explicitly capture the actual
host-active Compose file while the old crawler deployment still owns it. Do
this before merging or dispatching a change that can independently replace the
shared Compose file:

```bash
install -m 0644 /home/deploy/docker-compose.yml \
  /home/deploy/.crawler-active-docker-compose.yml
sha256sum /home/deploy/.crawler-active-docker-compose.yml \
  | awk '{print $1}' > /home/deploy/.crawler-active-docker-compose.sha256.tmp
chmod 0644 /home/deploy/.crawler-active-docker-compose.sha256.tmp
mv /home/deploy/.crawler-active-docker-compose.sha256.tmp \
  /home/deploy/.crawler-active-docker-compose.sha256
```

The deploy does not infer first-rollout state from a Git revision: a skipped or
failed prior deploy can leave the host behind Git. Missing or mismatched
snapshot evidence fails closed before mutation. Every successful crawler
deploy replaces both files for the next rollback. The deploy also fails closed
before activation unless the installed reconciliation wrapper's content and
completed-install digest marker exactly match the required Typesense-only
wrapper contract.
Once activation is armed, ordinary errors, shell exit, SSH hangup, and
termination signals all run the same guarded rollback exactly once; the guard
is disarmed only after service readiness and the durable Compose snapshot pass.

Least privilege is enforced at each runtime surface:

- long-running HTTP/browser workers, exporter, drain, and Alloy receive neither
  database variable;
- Alembic receives only `LOCAL_DATABASE_URL`, and Typesense schema setup
  receives only its four Typesense settings;
- deploy/CSV registry sync receives local Postgres, the separately named
  web-owned watchlist credential, and Typesense settings; CSV sync builds a
  mode-`0600` filtered env instead of passing the host file wholesale;
- `crawler export` always passes no relational-mirror pool and advances only
  `typesense:job_posting`;
- the production CLI exposes no legacy sync selector, no Supabase
  reconciliation target, and no relisted Supabase repair command; and
- the host reconciler always passes `--target typesense`, copies no web/mirror
  URL, and host metrics publish only the Typesense state row.

After deploying this slice but before dropping `public.job_posting`, verify the
new `/home/deploy/.env` has exactly one non-local database boundary named
`WEB_DATABASE_URL`, no `DATABASE_URL` line, and that inspected long-running
container environments contain neither. Do not print values while collecting
evidence. Verify deploy and CSV logs show plain `crawler sync`, exporter logs
show `exporter.typesense_enabled` plus `exporter.relational_mirror_disabled`,
and reconciliation command/journal evidence contains `--target typesense`.

Rollback-compatible Supabase library code remains temporarily in
`exporter.py`, `sync.py`, `reconciliation.py`, `repair_relisted_cdc.py`,
`bootstrap.py`, `db.py`, and the obsolete cursor/state/metric tests. It is not
reachable from deployed normal commands. Remove it, the `database_url` setting,
the Supabase reconciliation state row/schema allowances, and obsolete metrics
after the rollback window and contract migration are complete.

## Local location source repair (#6282)

The historical taxonomy bootstrap copied all 37,526 retained `location` IDs
but omitted `slug`, `lat`, and `lng`. An ordinary crawler deploy therefore
continues to refuse only the location Typesense refresh and preserves the
previous live collection; it must not deploy the #6256 location schema change
until the local source has been repaired.

Migration 0017 installs `chk_location_slug_nonblank` as `NOT VALID`. Existing
historical blanks do not block that release, while every later insert or
update is prevented from introducing another blank slug. The protected repair
holds a repeatable-read source snapshot and one serializable local transaction,
proves exact 37,526-row ID parity, rejects source defects and populated local
conflicts, updates only missing fields, proves exact field equality, and then
validates the durable constraint before committing.

Run only after the repair release has deployed successfully from `main`:

1. Record the exact deployed 40-character main SHA.
2. Open **Actions → Repair Local Location Taxonomy Source (Hetzner) → Run
   workflow** on `main`.
3. Enter that SHA as `expected_crawler_revision` and enter the exact token
   `REPAIR-LOCAL-LOCATION-TAXONOMY-37526`.
4. Approve the reviewer-gated `production-migrations` environment. The
   preauthorization job deliberately rejects non-owner,
   rerun-as-other-actor, non-main, wrong-revision, and wrong-token dispatches
   before requesting this approval. Do not use the non-review-gated
   `production` environment for this mutation.
5. Retain the bounded JSON evidence showing 37,526 source and local rows,
   `source_local_equal=true`, and `constraint_validated=true`.

The workflow shares both the crawler deployment concurrency group and
`/run/lock/jobseek-crawler-mutation.lock`. It verifies PostgreSQL recovery
headroom, exact deployed revision, immutable crawler image tag, exporter image
identity, and absence of relational credentials from the exporter. The repair
container receives only `LOCAL_DATABASE_URL` and the separately named retained
`WEB_DATABASE_URL`; neither value is printed or copied to GitHub.

After this evidence is attached to #6282/#6170, deploy #6256, require
`typesense.locations.synced count=37526` and the full taxonomy verifier, and
only then permit #6258. Do not combine this repair with the #6256 producer
schema change.

## Free-plan guardrails

Supabase's current platform documentation was rechecked on 2026-08-03. The
Free plan allows 500 MB of database data per project and 1 GB of underlying
disk; crossing 500 MB can put the database into read-only mode. The destructive
cutover therefore requires `pg_database_size(current_database()) < 400 MB`,
leaving at least 20 percent headroom rather than treating 500 MB as a target.
The organization must also remain within the two-active-Free-project limit.

Free projects do not receive Supabase automatic backups and may be paused after
seven days of low database activity. The encrypted six-hour logical backup,
restore drill, freshness alert, and a post-downgrade write canary are therefore
hard prerequisites. The 24-hour and 7-day checks must include database
writability, backup freshness, and project status; ordinary production traffic
is expected to keep the project active, but that is monitored rather than
assumed.

The organization downgrade is immediate and owner-controlled under **Billing →
Subscription Plan → Change subscription plan**. Before confirming it, record
the current billing cycle, active project count, add-ons, database size, and
latest successful restore evidence. Supabase credits unused prepaid plan time
but charges accrued overages at the plan change, and average daily usage can
remain elevated until the billing cycle resets. Record the resulting Free-plan
state and invoice/credit outcome without copying payment details into the
repository or issue.

Authoritative references:

- [Database and disk size](https://supabase.com/docs/guides/platform/database-size)
- [Subscription management](https://supabase.com/docs/guides/platform/manage-your-subscription)
- [Free-project pausing](https://supabase.com/docs/guides/platform/free-project-pausing)
- [Billing and plan quotas](https://supabase.com/docs/guides/platform/billing-on-supabase)
