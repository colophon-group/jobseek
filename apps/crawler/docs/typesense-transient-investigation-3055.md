# Investigation: Typesense `is_active:false` transient for a whole company / watchlist (issue #3055)

## TL;DR

**Bug: likely (transient), not confirmed.** A single code path can flip every active posting for a given board to `is_active=false` in one transaction (`_DELIST_BOARD_POSTINGS`, `apps/crawler/src/queries/monitor.py:86-91`). When that happens, the exporter ships those updates to Typesense within ~1 second (`export_interval=1`, `export_batch_limit=2000`). For a small watchlist whose tracked companies all run on a single board each (e.g. `swe-zurich` with 5 companies × ~25 postings each = 127 active), this manifests as a watchlist-wide `active=0` window if multiple boards delist near each other, or as a per-company `active=0` window if the user filter happens to scope to one company.

The "right" fix is **not** in the exporter or in Typesense — both behave correctly. The fix is in the web app: add a Postgres fallback for the year-count (issue #3056) AND consider a sanity check on the active-count when it's structurally implausible (year-count > 0 but active = 0 for a public, currently-listed watchlist).

The exporter does NOT have a "deactivate-all then re-activate" window. Typesense aliases are never atomically swapped under load (only the manual `--force` setup-typesense path drops + recreates). The `refresh-typesense` cron does not touch `job_posting` `is_active` values.

## Hypothesis ranking

1. **Most plausible — bulk per-board delist + small watchlist:** `_DELIST_BOARD_POSTINGS` flips all `is_active=true` postings for a board to `is_active=false` in a single statement. Exporter ships these to Typesense within ~1s. For a watchlist tracking 5 companies, each on a single board, simultaneous failures (or near-simultaneous within a few exporter ticks) would briefly leave the watchlist with `is_active:true` count ≈ 0 while the year-count (no `is_active` filter) is still high. Recovery happens when the board's next successful crawl re-lists postings via the `relisted` branch in `_DIFF_BATCH`.

2. **Possible but weaker — single-board delist of the entire watchlist:** if all 5 watchlist companies happened to be tracked by the same board (e.g. a multi-company aggregator), a single `_DELIST_BOARD_POSTINGS` would flip them all at once. The swe-zurich set is unlikely to share a single board (different ATSes), so this only applies to specific aggregator-based watchlists.

3. **Unlikely — index-drift schema patch rebuild:** `_patch_missing_fields` (`apps/crawler/src/typesense_schema.py:286-351`) does a drop+re-add of any field whose `index` flag differs between live and spec. If `is_active`'s `index` setting were ever inverted server-side, this would empty the field globally until the next exporter pass repopulates each doc. But: this is fleet-wide (not company-scoped), and the schema spec has never declared `is_active` as `index=False`, and it would have caused total-active-count = 0 in Grafana metrics (`typesense_export_lag` + reconciliation discrepancies), so we'd have noticed.

4. **Ruled out — alias swap / reindex:** `setup_collections` only drops + recreates the alias under `--force` (manual, never run in production by cron). The deploy.sh path runs without `--force` — it only PATCHes. So no alias swap window in normal operation.

5. **Ruled out — `refresh-typesense` cron impact on `is_active`:** the cron path (`apps/crawler/src/cli.py:324-343`) calls `refresh_typesense_counts` and `sync_watchlists_typesense`. Neither touches `job_posting.is_active` — `refresh_typesense_counts` only updates `*_posting_count` fields on taxonomy + company docs (`apps/crawler/src/sync.py:2382-2512`), and `sync_watchlists_typesense` only writes to the `watchlist` collection.

## Evidence

### 1. Where `is_active=false` is set in bulk

The only bulk-update path that flips many `is_active=true` rows to `false` in a single statement:

`apps/crawler/src/queries/monitor.py:86-91`
```sql
UPDATE job_posting
SET is_active = false, next_scrape_at = NULL, updated_at = now()
WHERE board_id = $1 AND is_active = true
RETURNING id
```

Callers (`apps/crawler/src/processing/board.py`):
- Line 1191 — empty-check threshold reached (6 consecutive empty cycles → board flipped to `gone`).
- Line 1313 — `BoardGoneError` (upstream 404 on a per-board API endpoint).
- Line 510 — `_maybe_delist_after_disable` (5-strike auto-disable, gated by `_DELIST_AFTER_FAILURE_AGE = 24h` last-success).

**Important: `_DELIST_BOARD_POSTINGS` is NOT behind the blast-radius / drop-threshold guards.** Those guards (`apps/crawler/src/processing/board.py:369-477`, `_mark_gone_with_guards`) only protect `_MARK_GONE_BY_TIMESTAMP`. The per-board delists above are "I know this entire board is gone, kill them all" — for a multi-company watchlist tracking one-board-per-company, this means the delist is unguarded per company.

`_MARK_GONE_BY_TIMESTAMP` (`apps/crawler/src/queries/monitor.py:300-319`) bumps `missing_count` and only flips to inactive when `missing_count >= delist_threshold` — gradual, per-posting, AND guarded.

### 2. Exporter shipping window

`apps/crawler/src/exporter.py:592-658` (`_export_postings_dual`):
- `settings.export_interval = 1` second (`apps/crawler/src/config.py:57`).
- `settings.export_batch_limit = 2000` (`apps/crawler/src/config.py:58`).
- Keyset pagination on `(updated_at, id)`.

A board delist of N rows (typical: 50-500) ships in 1 tick. For `swe-zurich` (127 across 5 boards), even if all 5 fired at once, all 127 ship within a single 1s tick. So the "all-false" window is **sub-second from Postgres's perspective**, but Typesense's `import_` import is batched server-side too.

The exporter has no "deactivate then re-activate" pattern. Each doc upsert carries the current row's `is_active` value (`apps/crawler/src/exporter.py:385`). No intermediate state.

### 3. Why the year-count survives

`first_seen_at:>1y` (year-count filter) does not include `is_active:true`. From `apps/web/src/lib/actions/watchlists.ts:1203`:
```ts
const parts = [POSTING_FLOW_FILTER, `first_seen_at:>${oneYearAgo}`];
```

where `POSTING_FLOW_FILTER = "has_content:!=false"` (`apps/web/src/lib/search/typesense-filters.ts:43`).

So even when every doc for a company has `is_active=false`, the year-count still matches because it doesn't filter on `is_active`.

### 4. Web-side cache amplification

`resolveFilteredJobCount` (`apps/web/src/lib/actions/watchlists.ts:915-953`) caches at 300s. This is for the watchlist LIST surface (the "X active" badge on each watchlist card on the discovery page), not the watchlist DETAIL page.

The detail page uses `getWatchlistPostings({offset: 0, limit: 20}).total` directly (no helper cache wrapper) and `getWatchlistPostingYearCount` (no cache). So the detail-page numbers are live per request.

The PAGE itself has `cacheLife({ revalidate: 3600 })` (`apps/web/app/[lang]/(app)/[userSlug]/[watchlistSlug]/page.tsx:143`), but `WatchlistContent` is a client component that fires `fetchWatchlistPageData` from `useEffect` — that's a fresh Server Action POST per page mount, so it doesn't sit in the cached payload.

So the cache amplifier theory is **mostly false** for the detail page. The transient is visible only when the user happens to hit refresh during the brief window.

### 5. Typesense alias / schema swap analysis

`setup_collections` (`apps/crawler/src/typesense_schema.py:354-387`):
- With `--force`: drops alias, drops versioned collection, creates new one, creates new alias. (NOT used in any cron.)
- Without `--force`: only patches (`_patch_missing_fields`). Calls `client.collections[name].update({"fields": ...})` with a PATCH payload.

The PATCH path can rebuild a field via a single drop+add pair (`apps/crawler/src/typesense_schema.py:332-335`) — but only when `_index_drift` (line 230-237) flags it. The check normalizes default `index=true` against live. For `is_active` (schema: `{"name": "is_active", "type": "bool", "facet": True}` — no explicit `index`), `_FIELD_INDEX_DEFAULT = True` means the desired is "indexed". The check only fires if live says `index: false`. **None of the historical migrations touch `is_active`'s index flag**, so this should never fire.

The deploy.sh sequence:
1. Pull the requested crawler image tag.
2. Ensure Redis is up.
3. Run alembic migrations.
4. Run `crawler setup-typesense` (idempotent PATCH).
5. Stop workers / exporter / drain / browser (Redis + alloy stay up).
6. Run `crawler sync` (CSV → Postgres + Redis + Typesense taxonomies + companies + watchlists).
7. `docker compose up -d`, force-recreate alloy, and gate core services before the workflow promotes the image tag to `latest`.

The `sync` step touches taxonomy + company + watchlist collections, NOT `job_posting`. So during a deploy, the `job_posting` collection's `is_active` field is unchanged. The exporter pauses (it was stopped in step 5) — when it restarts in step 7, it resumes from its persisted cursor. No fleet-wide is_active flip.

### 6. Live cluster snapshot (2026-05-13)

```
job_posting alias points to: job_posting_v1
total docs:                  1,546,333
is_active:true filter:         766,558
```

`is_active` field is working correctly — no global rebuild needed.

`swe-zurich` watchlist (id `4dac010b-4d4f-4ca1-9e82-058260c3b3e5`): `active_job_count=127`, `company_count=5`. So 5 companies × ~25 active postings each. A single board delist for one company would drop ~25 from 127. All 5 would need to delist concurrently to reach 0.

## Bug or not a bug?

**Bug: yes, but the bug surface is mis-located.**

- The Postgres + exporter behaviour is **correct**: when a board genuinely goes 'gone', its postings SHOULD be marked `is_active=false`. The exporter SHOULD ship those changes.
- The Typesense state is **consistent**: docs reflect Postgres truth within ~1s.
- The **web-side bug** is treating `active=0` from Typesense as authoritative when it could be a transient. A defensive year-count is decorative; the active-count drives whether the page says "no jobs" or shows a list.

The "active=0 · 396 last year" symptom is actually a TRUE STATE briefly:
- Postgres said: "all 5 companies' boards are temporarily gone, every posting is inactive."
- Year-count is timestamped and ignores activeness, so it returns the historical count.

The **real fix** is asymmetric: when active=0 but year-count > 0 AND the watchlist is public/featured/long-lived, treat it as a Typesense staleness hint and either:
(a) Re-fetch with a small delay and use the second answer.
(b) Fall back to a Postgres count for the active number.
(c) Display a generic "Indexing in progress — refresh in a moment" hint instead of an empty list.

## Fix complexity (if web-side)

The cleanest fix is **#3056**: add a Postgres fallback to `getWatchlistPostingYearCount`. But that doesn't address the underlying transient — it just makes the year-count more reliable.

A second-order fix would be:
- `getWatchlistPostings` already has a Postgres fallback for the active list (`_getWatchlistPostingsPostgres`, `apps/web/src/lib/actions/watchlists.ts:1148`).
- Postgres has `is_active = true` and `first_seen_at` columns, so a fallback path is mechanically simple.
- But Postgres falls behind Typesense in a different direction (the exporter is a CDC pipe with its own lag), so cross-checking against Postgres for "is this 0 trustworthy?" requires care.

## Recommended action items

1. **Close #3055 as "not a crawler bug; surface in web app"** with this analysis linked.
2. **#3056** (year-count Postgres fallback) addresses the visible 0-vs-396 asymmetry — fix as scoped.
3. **New issue: web-side defensive read for active-count = 0**. Possible designs:
   - "Active = 0, year-count > 0" emit a structured log + Prometheus counter so we can see how often this triggers. If rare, decorative copy ("Indexing in progress…") is enough. If frequent, real fallback is needed.
   - Or: cache `(active, year)` together with a longer TTL ONLY when they're consistent (year ≥ active). Reject the cache entry if the live read shows `(0, >N)` — that's a transient, don't poison the cache.
4. **No crawler changes needed** for this issue. The blast-radius guards (#2724) already protect `_MARK_GONE_BY_TIMESTAMP` against accidental mass-tombstoning. The bulk-delist paths (`_DELIST_BOARD_POSTINGS`) are intentionally aggressive because they only fire on confirmed dead boards (6 empty checks, upstream 404, or 5-strike disable with 24h recency gate).

## Files cited

- `apps/crawler/src/exporter.py:592-658` — `_export_postings_dual` (no deactivate-then-reactivate window; concurrent Supabase + Typesense upsert via `asyncio.gather`).
- `apps/crawler/src/exporter.py:385` — `is_active` carried per-doc from Postgres row.
- `apps/crawler/src/queries/monitor.py:86-91` — `_DELIST_BOARD_POSTINGS` bulk delist.
- `apps/crawler/src/queries/monitor.py:177-267` — `_DIFF_BATCH` atomic touch/relisted/new CTE.
- `apps/crawler/src/queries/monitor.py:300-319` — `_MARK_GONE_BY_TIMESTAMP` per-posting threshold (guarded).
- `apps/crawler/src/processing/board.py:283-298` — `_delist_board_postings` runner.
- `apps/crawler/src/processing/board.py:369-477` — `_mark_gone_with_guards` (drop + blast-radius).
- `apps/crawler/src/processing/board.py:1191,1313,510` — three `_delist_board_postings` call sites.
- `apps/crawler/src/typesense_schema.py:286-351` — `_patch_missing_fields` field rebuild logic.
- `apps/crawler/src/typesense_schema.py:354-387` — `setup_collections` (force vs patch).
- `apps/crawler/src/typesense_schema.py:37` — `is_active` field schema.
- `apps/crawler/src/sync.py:2382-2512` — `refresh_typesense_counts` (touches counts only).
- `apps/crawler/src/sync.py:2173-2313` — `sync_watchlists_typesense`.
- `apps/crawler/src/cli.py:324-343` — `refresh-typesense` CLI command.
- `apps/crawler/src/config.py:57-58` — `export_interval`, `export_batch_limit`.
- `apps/crawler/deploy.sh:108-129` — deploy sequence (stop → migrate → setup-typesense → sync → up).
- `apps/web/src/lib/actions/watchlists.ts:1168-1224` — `getWatchlistPostingYearCount` (no cache, no fallback).
- `apps/web/src/lib/actions/watchlists.ts:915-953` — `resolveFilteredJobCount` (TTL 300s — but for list pages only).
- `apps/web/src/lib/actions/watchlist-page-data.ts:119-122` — detail-page parallel fetch of postings + year-count.
- `apps/web/src/lib/search/typesense-filters.ts:23,43` — `POSTING_BASE_FILTER`, `POSTING_FLOW_FILTER`.
- `apps/web/app/[lang]/(app)/[userSlug]/[watchlistSlug]/page.tsx:142-143` — page-level 1h cache.
- `apps/web/app/[lang]/(app)/[userSlug]/[watchlistSlug]/watchlist-content.tsx:23-32` — client-side fetch trigger.

## Open empirical questions (not blocking the analysis)

- How often does `_DELIST_BOARD_POSTINGS` fire in production? Grafana panel `monitor_jobs_discovered{action="gone"}` shows the delist rate, but doesn't distinguish per-board-delist (one transaction, many rows) from per-posting delist (`_MARK_GONE_BY_TIMESTAMP`, gradual). A focused query on `board_status='gone' OR board_status='disabled'` transitions per day would establish the base rate.
- What is the per-board recovery time after a `_DELIST_BOARD_POSTINGS`? The `relisted` branch in `_DIFF_BATCH` reactivates on the next successful crawl, gated on `next_check_at`. For a board with `check_interval_minutes = 60`, the worst case is ~60 minutes of `is_active=false` for that board's postings, even if the underlying outage was a single bad cycle.
- Does the swe-zurich set actually run on 5 distinct boards, or do any share an ATS host? If they share, the blast radius is wider than 1 board.
