# ADR-002: Local Postgres for Crawler Runtime Source of Truth

Status: implemented

Date: 2026-07-07

## Context

The crawler runs on Hetzner close to a dedicated local Postgres instance.
Workers, processors, the exporter, reconciliation, and Typesense indexing all
depend on low-latency reads and writes of crawler-owned job data. Supabase is
still the web-facing Postgres service for user-owned tables such as auth,
sessions, watchlists, and posting detail fallbacks.

Historically, "just write to Supabase" looked tempting because the web app
already uses Supabase. That loses the crawler's operational separation and
creates split-brain risk between the worker pipeline, exporter CDC, Typesense,
and read-side fallbacks.

## Decision

Local Postgres is the authoritative store for crawler-owned runtime data,
including job postings, crawler state, board processing state, taxonomy lookup
inputs, and aggregation sources used for Typesense indexing.

For crawler-owned job data:

- Workers and processing code write local Postgres.
- Reprocessing and repair commands write local Postgres first.
- The exporter is the path that copies changed job data to Supabase.
- Typesense indexing and denormalization read from local Postgres.
- Supabase is treated as a read-side mirror for crawler-owned job data, not as
  the place to patch crawler truth directly.

This does not make Supabase read-only for the whole product. Web-owned auth,
session, preference, watchlist, subscription, and other user-facing tables are
still owned by the web app's Supabase/Drizzle layer.

## Consequences

- Production fixes to crawler-owned data should target local Postgres and let
  CDC/export/reconciliation catch Supabase up.
- Direct Supabase writes to crawler-owned job data need an explicit exception
  and a reconciliation plan.
- Supabase outages pause web mirror freshness but do not stop crawler workers
  from maintaining local truth.
- Typesense fallback and denormalization bugs should be debugged from local
  Postgres first, then exporter cursor state, then Supabase.

## References

- [Crawler architecture](../03-crawler-architecture.md)
- [System design](../07-system-design.md)
- [Typesense deployment state](../11-typesense.md)
- [`apps/crawler/src/exporter.py`](../../apps/crawler/src/exporter.py)
- [`apps/crawler/src/sync.py`](../../apps/crawler/src/sync.py)
