# Narrowed worker on Hetzner

Narrowed evaluation runs in one standalone Node.js process on Hetzner. Vercel
handles authenticated configuration/reconciliation requests and result reads.
An enqueue commits durable Postgres demand and an empty `pg_notify` payload in
one statement. The host listens for that notification and wakes immediately.
A missed notification is harmless: the host also checks Postgres every 30
seconds. Active work continues after two seconds; provider failures retain
exponential backoff and the existing database circuit/leases.

The host connects directly to Postgres, Typesense, R2 descriptions and TypeSafe.
It makes no requests to the Vercel refresh endpoint. The former endpoint returns
410 for dispatch; its authenticated read-only recovery inspection remains
compatible with older diagnostics. Web actions no longer call the classifier
through `after()`. The shared sweep also refuses execution inside Vercel.

## Runtime and credentials

`apps/web/scripts/build-narrowed-worker.ts` builds the existing matching,
classification and spend-check code into one artifact. A framework-free matcher
and read adapter are selected for this build; web callers retain their existing
Next.js taxonomy caches. The build rejects Next.js, React, Workflow and Vercel
runtime imports. Host taxonomy reads are shared only inside each bounded slice,
so successive checkpoints avoid redundant reads without pinning old taxonomy
across slices. The manifest binds source hashes, a clean Git revision, the
worker SHA-256 and the immutable runtime image in
`deploy/ai-filter-refresh/worker-runtime.json`.

The root-owned systemd launcher controls one Docker container. The container
runs as UID/GID 65532, has no Docker socket, no Linux capabilities, a read-only
filesystem, a small temporary directory, and CPU/memory/PID limits. Only the
worker artifact is mounted. Docker reads a root-only systemd env-file; the
worker receives an allowlisted JSON configuration. Never print that file,
container environment, provider responses or database URLs.

Provision these GitHub `production` environment secrets before cutover:

- `TYPESAFE_AI_TOKEN`: the existing production classifier credential.
- `AI_FILTER_CACHE_HMAC_SECRET`: **the same value currently used by production**;
  do not rotate the semantic-cache identity during this move.
- `NARROWED_WORKER_DATABASE_PASSWORD`: one stable, random base64url password
  containing 43–128 characters. Store it once; ordinary deployments reuse it.

If the HMAC exists only as a sensitive Vercel variable, Vercel cannot export it.
Keep the old worker running and block activation until a reviewed, authenticated
runtime transfer or cache-safe rotation procedure preserves decision reuse.
Do not substitute a new random value merely to satisfy the deployment check.

Existing protected database-owner, read-only Typesense, R2 domain, Vercel and
host-key-pinned SSH settings are reused by deployment. Non-sensitive feature
switches, stable-order receipt and spending limits are copied from the pulled
production settings. The worker receives no database-owner, Typesense write,
payment-provider, GitHub, or Cloudflare credential. The protected preparation
step creates `jobseek_narrowed_worker` on first activation, grants read access to
saved scopes/entitlements and read/insert/update access only to the AI tables,
and grants usage on the AI event sequence. It sets UTC, bounded statement and
idle-transaction timeouts, and six connections (two query connections, listener,
readiness session, and bounded deployment overlap). It does not change an
existing role's password. Rotate it only through a separately reviewed procedure;
changing the deployment secret alone fails the candidate preflight.

## Coordinated cutover

1. Prepare the three secrets above, preserving the existing token/HMAC values.
   Confirm the web database is at the checked-out migration head. This move adds
   no schema migration and retains saved queries, decisions, budgets and leases.
2. Merge the reviewed revision. The normal web workflow stages the new web
   artifact. It cannot promote while matching is enabled unless the **exact
   standalone worker bundle and current non-secret execution policy** hold their
   live Postgres readiness lock. Unrelated
   commits do not change the bundle identity; changing matching code or its
   bundled dependencies requires a corresponding host release first.
3. Dispatch `deploy-ai-filter-refresh.yml`, `action=activate`, with exact current
   main. It builds from that clean revision, verifies the migration head,
   prepares the restricted credential, and rechecks current main before install.
   The installer verifies both hashes and performs a read-only candidate
   preflight **before** stopping the previous HTTP driver. It installs an
   immutable release, disables the legacy minute timer, starts the standalone
   service and requires container health. Any failed activation restores prior
   files, credentials, release pointer and unit enablement.
4. Confirm the service/container is healthy and has emitted
   `narrowed_worker_ready`. The old and new execution paths use the same database
   singleton during overlap; existing leases prevent duplicate classification.
5. Let the web workflow promote, or rerun it if it reached the readiness gate
   before host activation. Confirm an owner edit queues work, the notification
   wakes the host, and current-revision decisions appear. Test first-page and
   pagination progress, concurrent owners, provider failure/backoff, edits during
   a slice and host restart. Confirm a newly indexed eligible posting receives
   a decision while its owner is away. A 200 or a readiness lock alone is not
   classification/freshness acceptance evidence.
6. Confirm the Vercel refresh route receives no periodic traffic and creates no
   classification work. Compare a complete post-cutover UTC day with aligned
   billing and route tables. This change removes the refresh Function pool,
   not company-page rendering, public APIs or middleware. Do not call the entire
   previous refresh CPU pool achieved savings before measuring replacement work
   and user-facing behavior.

The web workflow intentionally fails closed on a missing/mismatched host worker;
it does not automatically activate a worker or weaken maintenance controls.
When the source worker pause or either production execution switch is off, the
readiness gate reports disabled and the web workflow stops/disables the host
service through the existing protected pause operation before promotion.
Changes to spending limits or provider entitlement environments require a host
activation with the matching policy before enabled web code can promote.
Changing a Vercel environment variable without deploying still cannot stop an
already running host process.

## Inspection, pause and rollback

Use `action=verify` with current main and a watchlist UUID. The
protected operation reports the installed revision and invokes the host's
read-only `--inspect` command through temporary systemd credential delivery.
It returns current query revision, scope fingerprint, counts and the legacy
worker fence, never query text or owner identity.

`action=pause` stops/disables only `jobseek-ai-filter-refresh.service` and its
legacy timer, including boot enablement. It does not touch crawler writers,
other scheduled work, saved criteria or paid decisions. A source maintenance
release and subscription controls remain independent as described in
[narrowed-maintenance.md](narrowed-maintenance.md).

A failed candidate installation automatically restores the previous release.
An intentional reversal across this cutover must restore a compatible reviewed
web revision **and** its host installer through the protected workflow. The
retired HTTP driver cannot work against a web revision whose dispatch endpoint
returns 410. Never restore it by editing a live unit or copying mutable files.
