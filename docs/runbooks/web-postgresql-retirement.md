# Web PostgreSQL on Hetzner

The October 28 operator request authorizes moving the complete Jobseek web
database off Supabase, without a Supabase upgrade. The existing PostgreSQL
host has a separate data Volume and enough measured memory/disk headroom for
a resource-limited PostgreSQL 17 service. The crawler's PostgreSQL 16 instance
and its deployment/backup contract remain separate.

The reviewed implementation is `deploy/web-postgresql/`. All production
operations use `operate-web-postgresql.yml` on exact current main as the owner.
They require `WEB-POSTGRESQL-` plus the action in uppercase, including its
hyphen, as the confirmation. No credentials, host addresses, row contents, or
provider response bodies belong in logs or commits.

## Preparation and recovery proof

1. Preserve the existing Narrowed worker password. Provision stable, independent
   `WEB_POSTGRES_OWNER_PASSWORD` and `WEB_POSTGRES_AUDITOR_PASSWORD` secrets in
   GitHub's `production` environment, using 43–128 base64url characters each.
   Provision the authorized zone DNS credential as `CLOUDFLARE_DNS_API_TOKEN`
   and the crawler's existing private address as `CRAWLER_PRIVATE_IPV4` there.
   Never rotate an existing database identity by rerunning prepare.
2. Merge the reviewed tooling after required checks pass. Every workflow dispatch
   rechecks current main before mutation and transfers a SHA-256-verified bundle
   over existing strictly pinned OpenSSH.
3. Dispatch `recover-repository` on the Typesense/backup host. It holds both
   consumer data locks and the deployment lock, refuses any live local Restic
   process, and invokes ordinary stale-lock cleanup. It never force-removes
   live locks. The October 28 audit found both backup services inactive, no
   Restic process, and a lock whose local PID had been absent since October 16.
4. Dispatch `backup-source`, then `restore-source`. The version-4 packet includes
   **all** ordinary tables in `public` and `drizzle`, rather than the legacy
   19-table allowlist. It includes AI decisions/criteria/budgets, notification
   idempotency/quota state, subscriptions/payment state, sessions and migration
   state. New ordinary application tables enter the boundary automatically.
   External FKs, unreviewed policy roles, large objects, partitioned/foreign/
   materialized relations fail closed for a reviewed contract extension.
5. Dispatch `prepare` on the PostgreSQL host. It checks the dedicated Volume,
   memory, disk headroom, port and existing identity before installing the
   separate pinned PostgreSQL 17 container. It has a 1.5 GiB memory ceiling,
   one CPU, no capabilities or Docker socket, bounded logs and a read-only
   image. Its data is on the existing attached Volume. Only the new TLS/SCRAM
   endpoint is admitted; the crawler's listener is untouched.
6. The new endpoint uses a DNS-only record, a publicly trusted ACME certificate,
   mandatory TLS and independent owner/auditor/worker logins. Certificate
   renewal copies protected certificate material and reloads this service.
   The worker login is limited to the crawler's known public/private sources.
7. Dispatch `rehearse`. It creates a uniquely named temporary database, takes
   a complete source snapshot, restores with the restricted destination owner,
   compares table fingerprints and sequence bounds, preserves the exact source
   auditor/worker table and sequence grants, and removes the temporary database.
   Keep the successful aggregate evidence. A local production-data rehearsal on
   October 28 restored 54 tables / 784,784 rows from a 26.2 MB custom archive.

A packet uses one exported repeatable-read source snapshot for its catalogue,
row fingerprints and `pg_dump`. Sequence values are nontransactional: the
restored values must lie within the observed monotonic before/after bounds;
a reset fails the backup. UTC, ISO date style, hexadecimal bytea and maximum
float precision are explicit, avoiding source pooler defaults. Source/destination
hashes are count plus two 64-bit multiset sums per table; row values never enter
the evidence. The custom archive is SHA-256-bound and encrypted off host by
Restic. Restore proof reads the encrypted snapshot into a disposable, networkless
PostgreSQL 17 instance, and checks cleanup.

## Cutover

Preparation and rehearsals leave production on Supabase. Finish them before
beginning the bounded write-maintenance window.

1. Pause Narrowed using the existing exact-head `deploy-ai-filter-refresh.yml`
   `action=pause` operation. Do not change subscription collection or purchase
   settings. The copy refuses live source worker sessions.
2. Dispatch `copy` at exact current main. This operation shares
   `deploy-web-production` concurrency so a web deployment cannot race the
   credential switch. It first requires a fresh encrypted full backup and an
   isolated restore proof on the backup host.
3. The source transaction installs statement-level write fences on every
   application/migration table, draining existing DML. Old web deployments and
   other writers cannot bypass the fence. Public search remains independently
   available, while database mutations must retry during the window.
4. A fresh full snapshot is restored once into an empty destination. The source
   fencing schema is excluded and its exact named triggers are filtered from
   the restore list for both application and migration schemas. The destination
   must pass all data/sequence/role checks before its copy receipt is published.
   A populated or previously copied destination cannot be overwritten.
5. While the copy workflow retains the deployment group, the authorized
   coordinator updates Vercel's production `DATABASE_URL` and
   `DATABASE_URL_UNPOOLED`, GitHub `production`'s same secrets, and
   `production-migration-drift`'s `DATABASE_URL_READONLY`. Both destination URLs
   use hostname/certificate verification. Preserve environment IDs, scopes,
   sensitivity and branch overrides. Account for configured preview/development
   consumers and protected local stores as part of retirement.
6. The coordinator sends `host.py confirm-consumers` a root-only receipt binding
   the copied revision, exact owner/auditor URL hashes and consumer scopes.
   This receipt is an attestation by the authorized coordinator after successful
   provider API updates, not independent proof of provider state. Re-read the
   affected provider metadata and verify the resulting deployment at runtime.
   The copy workflow holds its group for up to ten minutes waiting for this
   receipt. Timeout or interruption after a successful copy leaves source
   writes fenced because an external update might already have committed.
7. Activate Narrowed through its existing workflow using the new protected
   database setting. It provisions/verifies its restricted role and holds the
   existing bundle/policy readiness lock in the new database. Preserve the
   cache HMAC and provider credentials; no AI identity rotation is needed.
8. Dispatch the normal web production workflow with `rebuild=true`. Its migration
   head, worker readiness, build, scanner and promotion checks still apply.
   Verify canonical production, login/session continuity, watchlists/saved jobs,
   subscription entitlement and webhook writes, notification idempotency state,
   and real new matching decisions in the new database. A 200 alone is not
   database-cutover acceptance.
9. Dispatch `activate-backup`. It quiesces the legacy web backup timer, changes
   its protected database credential and runtime to the complete packet engine,
   proves a new encrypted Hetzner backup plus isolated restore, then enables the
   existing six-hour timer. Failure leaves that timer disabled and status failed.
   The existing backup metric names and fleet attribution remain unchanged.
10. Confirm no active web/worker/backup/migration consumer points to Supabase.
    Retain the fenced source as the rollback floor while the new runtime and
    backup evidence are accepted. Do not delete the project or its data as part
    of initial cutover. Do not treat a prior Supabase deploy as a safe rollback
    after destination writes have started.

## Failure and rollback

Before a successful copy receipt exists, an ordinary copy failure, termination
or hangup removes its own source fence. No external credentials have been
switched at that point. A failed restore remains isolated and cannot overwrite
an existing populated target. Retain protected diagnostics; repair/rehearse
before retrying. Never delete a populated destination without proving it has
not accepted production writes.

After the successful copy receipt is published, source stays fenced until an
explicit coordinated rollback or retirement decision. If consumer changes are
partial, finish/reconcile them under the deployment boundary. Once destination
writes exist, rollback requires preserving/replaying those writes; reverting
URLs or simply removing the source fence would create divergent primaries.
A killed process or host outage requires inspecting the durable copy/fence/
consumer receipts. A ten-minute deadline is not evidence that external writes
did not occur.

The version-4 runtime supersedes the historical company-reference rehearsal/
19-table backup operator tools for current production. Those historical packets
and routines remain available for their own rollback floors. Do not reinstall
the legacy selective runtime over an accepted complete backup without a new
reviewed recovery decision. Existing backup deployment automation remains
manual and does not reactivate the retired Supabase credential automatically.
