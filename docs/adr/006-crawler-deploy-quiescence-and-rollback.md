# ADR-006: Crawler Deploy Quiescence and Rollback

Status: implemented

Date: 2026-07-07

## Context

Crawler deploys update long-running workers, browser workers, exporter, drain,
Redis-backed schedules, Typesense schema state, and local Postgres migrations.
The deploy cannot be treated as a pure zero-downtime process because `sync`
must reseed Redis-backed schedules while processors are not claiming work.

`apps/crawler/deploy.sh` therefore pulls and preflights while the old stack is
still serving, then quiesces processors, runs sync, starts the full stack, and
gates readiness. Earlier deploy incidents showed that failures in the middle of
this sequence can create a dark window if rollback and monitoring are weak.

## Decision

Treat crawler deploys as a bounded quiescence window with explicit rollback and
readiness gates, not as an atomic swap.

The deploy script must:

- validate required environment before stopping processors;
- preserve a rollback copy of the env file;
- pull images and run schema preflights before quiescing processors;
- stop workers, browser worker, exporter, and drain before `crawler sync`;
- start the full stack after sync;
- wait for core services to be running or healthy;
- restore the previous env and start compose services on failure.

## Consequences

- Deploy changes need failure-path review as much as happy-path review.
- Monitoring should alert when crawler metrics disappear or exporter freshness
  stalls after a deploy.
- Operators should assume a mid-deploy failure may require checking compose
  state, Redis, exporter freshness, and logs before retrying.
- Future zero-downtime deploy work should preserve the Redis reseed invariant or
  explicitly replace it with an equivalent safe handoff.

## References

- [`apps/crawler/deploy.sh`](../../apps/crawler/deploy.sh)
- [Crawler architecture deploy notes](../03-crawler-architecture.md)
- [Typesense deploy notes](../11-typesense.md)
- [Crawler alert rules](../../apps/crawler/alerts.yaml)
- [Crawler AGENTS operations notes](../../apps/crawler/AGENTS.md)
