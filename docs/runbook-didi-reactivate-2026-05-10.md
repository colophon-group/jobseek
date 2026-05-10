# Runbook — re-activate `didi-global-careers-intl` (2026-05-10)

Operational reactivation of the `didi-global-careers-intl` board, auto-disabled
by the 5-strike circuit breaker after a stale SSL-cert verify failure on
2026-04-23. Closes #2997.

The PR #2964 fix (`scraper_type=skip` -> `dom`, `enrich: ["description"]`)
is correct but couldn't take effect while the monitor row was disabled in
`job_board`. PR #2633 had already threaded `skip_ssl: true` through to the
api_sniffer browser path; the recorded `last_error` was from the pre-fix
window. This runbook is the missing operational step: clear the error
state in the DB and trigger a fresh scrape so the existing 1,979 stalled
postings get descriptions.

## Pre-state (Hetzner local Postgres `178.104.102.63`)

```
        board_slug        | is_enabled | board_status | consecutive_failures |        last_success_at
--------------------------+------------+--------------+----------------------+-------------------------------
 didi-global-careers-intl | f          | disabled     |                    5 | 2026-04-23 06:46:23.178085+00
 didi-global-careers-cn   | f          | disabled     |                   38 | 2026-04-14 08:10:11.928948+00

  total | has_content | is_active | scheduled | scraped
 -------+-------------+-----------+-----------+---------
   1979 |           0 |       367 |         0 |       0
```

`didi-global-careers-cn` was retired in #2632 (eightfold-style path now
returns 501; CSV row removed); the DB row is stale but harmless and
remains disabled. Only the `intl` board is reactivated here.

## Upstream probe (Hetzner crawler-box egress)

Both icims endpoints return healthy data:

```
$ curl -sk -X POST 'https://cdncareers.didiglobal.com:34003/icims/searchJobList' \
    -H 'sec-ch-ua-platform: "macOS"' -H 'referer: https://careers.didiglobal.com/' \
    -H 'user-agent: Mozilla/5.0 ... Chrome/133.0.0.0 Safari/537.36' \
    -H 'accept: application/json, text/plain, */*' \
    -H 'content-type: application/json' \
    --data '{"country":"","keyValue":"","teamId":"","typeId":""}'
HTTP=200 SIZE=172311 TIME=1.281s
{"success":true,"message":"success","code":200,"result":[{"id":"9281",...
```

## Module-level dry-run (production scraper module, browser worker)

```
$ docker exec deploy-browser-1-1 uv run --no-sync crawler board \
    didi-global-careers-intl --dry-run -v
{"event":"dry_run.monitor.done","urls":354,"rich":true,
  "enrich":["description"]}
{"event":"dry_run.scraper.result","status":"ok",
  "title":"CX Vendor Strategy Manager","description_len":7552,
  "locations":["Bogota - Colombia"]}
... (3/3 sample URLs OK)
```

Monitor surfaces 354 URLs with title + employment_type + metadata.team
(rich); scraper enriches description (5-8KB HTML) and locations from the
SSR `careers.didiglobal.com` HTML. Exactly the post-#2964 expected shape.

## Reactivation steps (already executed)

1. Re-enable the board row:

   ```sql
   UPDATE job_board
   SET is_enabled = true,
       consecutive_failures = 0,
       last_error = NULL,
       board_status = 'active',
       next_check_at = now(),
       updated_at = now()
   WHERE board_slug = 'didi-global-careers-intl';
   -- UPDATE 1
   ```

2. Inline single-board run (monitor + due scrapes):

   ```
   $ docker exec deploy-browser-1-1 uv run --no-sync crawler board \
       didi-global-careers-intl
   batch.monitor.success: discovered=354, processed=354, new=108,
                          relisted=6, gone=127, duration=14.57s
   single_board.complete: scraped=114, succeeded=114, failed=0
   ```

3. Reschedule the 1,979 stalled rows that #2964 left with
   `next_scrape_at IS NULL`:

   ```sql
   UPDATE job_posting
   SET next_scrape_at = now()
   WHERE company_id = (SELECT id FROM company WHERE slug = 'didi-global')
     AND is_active = true
     AND next_scrape_at IS NULL;
   -- UPDATE 367
   ```

4. Second pass to drain the queued scrapes:

   ```
   $ docker exec deploy-browser-1-1 uv run --no-sync crawler board \
       didi-global-careers-intl
   batch.monitor.success: discovered=354, new=0, relisted=0, gone=127
   single_board.complete: scraped=367, succeeded=240, failed=127
   ```

   The 127 failures match the `gone=127` count from the monitor — those
   are postings that no longer exist on `careers.didiglobal.com` (the
   monitor marked them `is_active=false` mid-pass).

## Post-state

```
        board_slug        | is_enabled | board_status | consecutive_failures |        last_success_at
--------------------------+------------+--------------+----------------------+-------------------------------
 didi-global-careers-intl | t          | active       |                    0 | 2026-05-10 08:51:52.433867+00

  total | has_content | is_active | scheduled | scraped | scrape_dead
 -------+-------------+-----------+-----------+---------+-------------
   2087 |         354 |       481 |       481 |     481 |           0
```

`has_content` climbed from 0 → 354. All 481 currently-active postings
have been scraped exactly once (no orphans); `scrape_failures >= 3` count
is 0 (no posting has tripped the per-row 3-strike). Typesense will pick
up the fresh `description_r2_hash` values via the CDC exporter on the
next tick (already running, ~2s/2000-row tick).

## Why this isn't a code change

Two fixes had to land before this runbook would work and they were
already merged:

- #2633 — threaded `skip_ssl: true` through api_sniffer's browser path
  (`open_page` + replay). Without this, `cdncareers.didiglobal.com:34003`
  fails with `CERTIFICATE_VERIFY_FAILED` (broken intermediate CA chain).
- #2964 — flipped `scraper_type` from `skip` to `dom` and added
  `"enrich": ["description"]` so rich api_sniffer postings get
  `next_scrape_at = now()` instead of `NULL`, and so the dom scraper
  actually dispatches.

The disabled state predates both fixes; once both are deployed (current
crawler version is 0.11.62, both PRs are well below that), all that's
needed is the manual `is_enabled=true` flip + the one-shot
`next_scrape_at = now()` to unblock the 367 stalled rows.
