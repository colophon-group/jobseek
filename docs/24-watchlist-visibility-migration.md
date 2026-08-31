# Watchlist visibility migration

This runbook covers the reversible data migration in #8369. It inventories
legacy public watchlists, makes those rows private in one transaction, and
retains exact rollback and URL-path evidence for #8370. It does not purge
caches, SEO/IndexNow state, Typesense documents, routes, rows, or columns.

## Hard deployment gate

Do not merge or deploy this migration before both conditions are evidenced:

1. #8376 is merged, deployed, and verified so creates/copies are private and
   copy authorization no longer depends on `is_public`.
2. #8368 has passed the human UI review, its owner-only route cutover is
   deployed, and anonymous/cross-owner probes return the same not-found result.

#8367's anonymous REST/MCP cutover must also be deployed as required by #8369.
The PR is intentionally draft and no workflow invokes the migration. The
rendered-UI and cost gates are not applicable to this data-only PR; the human
approval attached to #8368 remains a prerequisite.

The migration reviewer and rollback owner must be two named people. The
rollback owner stays available for the observation window and owns the final
go/no-go call.

## Preflight and inventory

Use a direct, unpooled production connection. Record the successful protected
PostgreSQL backup/restore-drill run ID and verify its restored copy contains
both `watchlist` and `watchlist_company` with matching counts/checksums.

Create the sensitive inventory on encrypted operator storage; never attach it
to a public PR, Actions summary, or ordinary logs:

```bash
cd apps/web
umask 077
DATABASE_URL_UNPOOLED="$DATABASE_URL_UNPOOLED" \
  pnpm db:migrate:watchlist-visibility -- \
  inventory /secure/watchlist-0089-inventory.json
```

The artifact includes every public row ID, owner ID/name, canonical username,
display username, slug, full watchlist payload (filters, alerts and source
provenance included), exact company memberships, and page/OG paths for `en`,
`de`, `fr`, and `it`. Record its public count, digest, filesystem checksum,
owner, and retention deadline in the private change record.

Go only when the inventory command passes, the backup restore evidence is
fresh, #8376/#8368/#8367 deployment evidence is immutable, no privacy/API
probe fails, no database alert is firing, and both reviewers are present.
Abort on any missing/different ledger row, schema/index drift, owner orphan,
inventory count/digest mismatch, lock timeout, backup uncertainty, route/API
exposure, or unavailable rollback owner.

## Apply and verify

Export the reviewed evidence without printing secrets or inventory contents:

```bash
export MIGRATION_REQUIRE_UNPOOLED=true
export WATCHLIST_PRIVACY_CONFIRMATION=PRIVATE-WATCHLISTS-0089
export WATCHLIST_PRIVACY_BACKUP_RESTORE_RUN_ID=<successful-run-id>
export WATCHLIST_PRIVATE_MUTATIONS_DEPLOY_SHA=<deployed-8376-sha>
export WATCHLIST_ROUTE_CUTOVER_DEPLOY_SHA=<deployed-8368-sha>
export WATCHLIST_ROUTE_CUTOVER_APPROVED_BY=<github-login>

pnpm db:migrate:watchlist-visibility -- \
  apply /secure/watchlist-0089-inventory.json
pnpm db:migrate:watchlist-visibility -- \
  verify /secure/watchlist-0089-inventory.json \
  /secure/watchlist-0089-postflight.json
```

The targeted runner, not generic `db:migrate`, owns this operation. It takes
the web migration advisory lock and supplies a session-local attestation. SQL
then locks `watchlist`, `watchlist_company`, and owner rows; stores the durable
rollback/path inventory; updates only `is_public=true`; and compares exact
row, filter, alert, provenance, owner, and membership digests/content before
commit. Any mismatch rolls back the transaction and its ledger row.

During the observation window, verify:

- migrated owners can list and open every watchlist while signed in;
- anonymous and different-owner page, REST, and MCP probes are uniformly
  not-found and never reveal titles, filters, companies, or owner metadata;
- public-watchlist counts stay zero in Postgres while total watchlists,
  memberships, alerts-enabled rows, and copied/source-linked rows match the
  postflight artifact;
- database errors/latency, Vercel route errors, auth failures, and alert
  delivery volume show no regression.

Do not run cache, Typesense, sitemap, OG, or IndexNow cleanup here. Hand the
retained `pathVariants` inventory to #8370 only after the observation window
is accepted. Keep `is_public`, `idx_wl_public`, and both 0089 artifact tables
until the rollback window closes; #8371 owns their later removal.

## Rollback

Rollback if any owner loses access, any protected count/digest changes, an
anonymous/cross-owner read succeeds, alerts regress, or the rollout produces
sustained database/route errors. Stop the rollout and preserve diagnostics;
do not purge caches or indexes first.

```bash
export MIGRATION_REQUIRE_UNPOOLED=true
export WATCHLIST_PRIVACY_ROLLBACK_CONFIRMATION=ROLLBACK-PRIVATE-WATCHLISTS-0089
export WATCHLIST_PRIVACY_ROLLBACK_OWNER=<responsible-operator>

pnpm db:migrate:watchlist-visibility -- \
  rollback /secure/watchlist-0089-inventory.json
```

Rollback locks the same tables, requires the exact retained inventory and
unchanged row/membership content, and republishes only IDs that were public in
the reviewed artifact. It leaves the migration ledger and rollback tables in
place for audit. If any protected row changed after migration, rollback aborts
rather than exposing altered content; restore the protected backup to an
isolated database and open a separately reviewed recovery change.

After rollback, verify the exact restored public count, owner access, legacy
public paths, and alert behavior. Do not re-run apply from the rolled-back
state; prepare a new reviewed recovery migration after the incident is closed.
