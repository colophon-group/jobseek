# ADR-006: Crawler Deploy Quiescence and Rollback

Status: implemented

Date: 2026-07-07

## Context

Crawler deploys update long-running workers, browser workers, exporter, drain,
Redis-backed schedules, Typesense schema state, and local Postgres migrations.
The deploy cannot be treated as a pure zero-downtime process because `sync`
must reseed Redis-backed schedules while processors are not claiming work.

`apps/crawler/deploy.sh` therefore pulls and preflights while the old stack is
still serving, then quiesces every local-Postgres writer plus the exporter,
runs migrations, patches Typesense, runs sync, starts the full stack, and gates
readiness. Earlier deploy incidents showed that failures in the middle of this
sequence can create a dark window if rollback and monitoring are weak.

## Decision

Treat crawler deploys as a bounded quiescence window with explicit rollback and
readiness gates, not as an atomic swap.

The deploy script must:

- validate required environment before stopping processors;
- preserve a rollback copy of the env file;
- pull images and run non-mutating preflights before quiescing processors;
- stop workers, browser worker, exporter, and drain before local Postgres
  migrations, Typesense schema patching, or `crawler sync`;
- record a deterministic runtime-contract digest in the committed release, and
  require standalone CSV syncs to match that digest before publishing config;
- commit one release generation that binds the verified runtime environment and
  immutable image identities to the exact applied CSV tree, its canonical
  per-file SHA-256 manifest, host-recomputable data contract, and source
  revision. The data contract is the SHA-256 of that canonical manifest: CI
  derives it from Git blob bytes and every host publication path recomputes it
  from the verified archive or image tree before sync, no-op, or promotion;
- reject an explicitly requested CSV revision unless its complete publishable
  CSV tree matches current `main`, while allowing runtime-only commits to have
  advanced since that revision;
- treat process-cached industry, occupation, seniority, and technology
  taxonomies as part of the runtime contract, so changing any of them requires
  a full image rollout;
- let the full deploy own CSV publication when one push changes both the
  runtime contract and crawler data;
- let a data-only publisher wait for a compatible deploy without holding the
  host mutation lock, then recheck the triggering revision's immutable runtime
  contract after acquiring the lock. If a later full deploy has already
  committed the candidate's exact data contract, finish as a no-op rather than
  waiting for the older runtime to return;
- transfer data-only candidates into run- and revision-specific host paths,
  verify the transferred archive and exact CSV tree on the host, run sync with
  that tree mounted read-only, and atomically select the complete generation
  only after sync succeeds;
- journal the sync-to-pointer-publication seam durably. An interrupted failed
  candidate restores the prior committed tree; an interrupted first-rollout
  bootstrap keeps retrying the exact pre-deploy tree rather than falling back
  to stale CSVs embedded in the legacy image;
- before the first format-v3 deploy, reapply the exact pre-push `main` CSV tree
  with the verified committed runtime and promote it as rollback evidence.
  After that transition, preserve the verified active format-v3 generation as
  the actual live rollback evidence even when later `main` CSVs differ;
- remove consumed candidate archives and prune stale candidate/generation
  residue under the mutation lock. Preserve the active generation, durable
  journal references, explicit rollback target, five newest fully verified v3
  generations, and a 48-hour grace window for staged or crash-interrupted
  work. Corrupt/partial generations do not consume the rollback window, and
  abandoned `.crawler-forward-data-*` image-extraction staging is pruned by the
  same locked routine;
- start the full stack after sync;
- wait for core services to be running or healthy;
- restore the previous env on failure and, if the forward sync began, mount and
  resync the previous generation's exact committed CSV tree read-only in the
  previous image before restarting old services. Pre-v3 generations are
  accepted only long enough to bootstrap an exact staged tree; deploy rollback
  never falls back to CSVs embedded in a legacy image.

## Consequences

- Deploy changes need failure-path review as much as happy-path review.
- Schema/runtime protocols can be cut over without old and new writers
  overlapping. The tradeoff is a longer bounded crawler-processing pause;
  Typesense stays available to web reads.
- Monitoring should alert when crawler metrics disappear or exporter freshness
  stalls after a deploy.
- Operators should assume a mid-deploy failure may require checking compose
  state, Redis, exporter freshness, and logs before retrying.
- Future zero-downtime deploy work should preserve the Redis reseed invariant or
  explicitly replace it with an equivalent safe handoff.
- The standalone CSV workflow can publish data-only revisions without an image
  build. It fails closed for stale, missing, extra, deleted, or tampered data
  evidence, mutable live-environment drift, and incompatible runtimes.
- A rollback after sync has begun keeps processors quiesced until the previous
  image has restored its own configuration; a failed restore leaves the old
  workers stopped rather than running against the newer configuration.

## References

- [`apps/crawler/deploy.sh`](../../apps/crawler/deploy.sh)
- [Crawler architecture deploy notes](../03-crawler-architecture.md)
- [Typesense deploy notes](../11-typesense.md)
- [Crawler alert rules](../../apps/crawler/alerts.yaml)
- [Crawler AGENTS operations notes](../../apps/crawler/AGENTS.md)
