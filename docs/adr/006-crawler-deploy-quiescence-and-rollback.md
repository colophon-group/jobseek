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
- reject an explicitly requested CSV revision unless its complete publishable
  CSV tree matches current `main`, while allowing runtime-only commits to have
  advanced since that revision;
- treat process-cached industry, occupation, seniority, and technology
  taxonomies as part of the runtime contract, so changing any of them requires
  a full image rollout;
- let the full deploy own CSV publication when one push changes both the
  runtime contract and crawler data;
- let a data-only publisher wait for a compatible deploy without holding the
  host mutation lock, then recheck the contract after acquiring the lock;
- start the full stack after sync;
- wait for core services to be running or healthy;
- restore the previous env on failure and, if the forward sync began, resync
  the previous image's embedded CSV snapshot before restarting old services.

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
  build. It fails closed for stale data snapshots, and waits for a pending
  compatible runtime deployment instead of publishing against the old image.
- A rollback after sync has begun keeps processors quiesced until the previous
  image has restored its own configuration; a failed restore leaves the old
  workers stopped rather than running against the newer configuration.

## References

- [`apps/crawler/deploy.sh`](../../apps/crawler/deploy.sh)
- [Crawler architecture deploy notes](../03-crawler-architecture.md)
- [Typesense deploy notes](../11-typesense.md)
- [Crawler alert rules](../../apps/crawler/alerts.yaml)
- [Crawler AGENTS operations notes](../../apps/crawler/AGENTS.md)
