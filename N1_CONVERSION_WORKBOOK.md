# N+1 Monitor → Monitor+Scraper Conversion Workbook

Convert monitors that list jobs then fetch details per-job (N+1 pattern) into
a two-phase design: **monitor** (hourly, returns URLs only) + **scraper**
(daily, fetches details via dedicated API).

## Why

Monitors run hourly. Detail data rarely changes. Hitting detail endpoints for
every job on every poll wastes requests and risks rate-limiting. Splitting into
monitor (cheap list call) + scraper (expensive detail calls on daily schedule)
reduces API load by ~24×.

## Reference Implementation: Workday

Completed conversion lives in:

| Component | File |
|-----------|------|
| Monitor (list-only) | `src/core/monitors/workday.py` |
| Scraper (detail API) | `src/core/scrapers/workday.py` |
| Scraper tests | `tests/test_workday.py` (TestParseDetail, TestParseJobUrl, TestScrape) |
| Registration | `src/core/scrapers/__init__.py` |
| Auto-config | `src/workspace/_compat.py` → `auto_scraper_type()` |
| Batch wiring | `src/batch.py` → `_load_board_scrapers()` |

---

## Conversion Steps

For each monitor, follow this checklist:

### 1. Extract detail-fetching code into a scraper

Create `src/core/scrapers/<name>.py`:

```python
"""<Name> detail API scraper."""
from __future__ import annotations
import httpx, structlog
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

def _parse_detail(data: dict) -> JobContent:
    """Parse detail API response into JobContent."""
    return JobContent(
        title=...,
        description=...,
        locations=...,
        employment_type=...,
        job_location_type=...,
        date_posted=...,
        salary=...,
    )

async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch job details from the <Name> detail API."""
    # 1. Derive detail API URL from the job page URL
    # 2. GET/POST the detail endpoint
    # 3. Parse response → JobContent
    ...

register("<name>", scrape)
```

Key decisions:
- **URL derivation**: The scraper receives the public job URL. It must derive
  the API detail URL from it (regex, slug extraction, etc.).
- **Field mapping**: Move all field parsing (description, locations, salary,
  employment type, location type) from the monitor into `_parse_detail()`.
- **Error handling**: Return empty `JobContent()` on failure, log a warning.

### 2. Simplify the monitor to return URLs only

Modify `src/core/monitors/<name>.py`:

- Remove all detail-fetching logic (the semaphore, detail HTTP calls, parsing).
- Change return type from `list[DiscoveredJob]` to `set[str]`.
- Keep only the list/search endpoint call + pagination.
- Register as `rich=False` (it no longer provides full data).
- Remove `CONCURRENCY` constant (no longer needed in monitor).

Before:
```python
async def discover(board, client):
    jobs = await _list_jobs(board, client)  # 1 call
    details = await _fetch_all_details(jobs, client)  # N calls
    return [DiscoveredJob(url=..., title=..., description=...) for ...]

register("example", discover, rich=True)
```

After:
```python
async def discover(board, client):
    urls = await _list_jobs(board, client)  # 1 call only
    return urls  # set[str]

register("example", discover, rich=False)
```

### 3. Register the scraper

In `src/core/scrapers/__init__.py`, add:
```python
from src.core.scrapers import <name> as _<name>  # noqa: F401
```

### 4. Wire up auto-configuration

In `src/workspace/_compat.py`:

- Add the type to `auto_scraper_type()` if it should auto-configure:
  ```python
  if monitor_type == "<name>":
      return "<name>"
  ```
- Remove it from `_RICH_MONITORS` if it was there.
- Ensure it's in `_ALL_MONITOR_TYPES`.

In `src/batch.py` → `_load_board_scrapers()`:
- The existing logic already handles this: `auto_scraper_type()` returns the
  scraper name, which is used directly. No changes needed if auto_scraper_type
  is updated.

### 5. Update agent instructions

In `src/workspace/commands/help.py`:
- Update the monitor card to reflect URL-only output.
- Add/update the scraper card with API details.
- Update the MONITORS table scraper column.

### 6. Write tests

- **Scraper unit tests**: `_parse_detail()` with sample API response, URL
  derivation, `scrape()` with mocked HTTP.
- **Monitor tests**: Update existing tests to expect `set[str]` instead of
  `list[DiscoveredJob]`.
- Run `uv run pytest tests/` — ensure `test_compat.py` consistency checks pass.

### 7. Backfill existing postings

After deploying the monitor change, existing postings will have descriptions
from previous rich-monitor runs but `next_scrape_at` will be NULL. Run a
one-off query:

```sql
UPDATE job_postings
SET next_scrape_at = NOW()
WHERE board_id IN (SELECT id FROM boards WHERE crawler_type = '<name>')
  AND next_scrape_at IS NULL
  AND active = TRUE;
```

---

## Per-Monitor Analysis

### Workable

| Aspect | Detail |
|--------|--------|
| List API | `POST https://apply.workable.com/api/v3/accounts/{slug}/jobs` |
| Pagination | Token-based (`nextPage` in response) |
| Detail API | `GET https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}` |
| Detail fields | title, description + requirements + benefits (HTML), locations, employmentType, workplace type, published date |
| URL → detail key | Extract shortcode from URL path (last segment) |
| Auth | None (public API) |
| Complexity | Low — clean REST APIs, no auth |

**URL format**: `https://apply.workable.com/{slug}/j/{shortcode}`
→ shortcode in path, slug from board config.

### SmartRecruiters

| Aspect | Detail |
|--------|--------|
| List API | `GET https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset=N` |
| Pagination | Offset-based (100/page) |
| Detail API | `GET https://api.smartrecruiters.com/v1/companies/{token}/postings/{postingId}` |
| Detail fields | jobAd sections (HTML), compensation (min/max/currency/period), employment type, locations |
| URL → detail key | Extract postingId from URL path |
| Auth | None (public API) |
| Complexity | Low — has retry logic (3 attempts, exp backoff) worth preserving in scraper |

**URL format**: `https://careers.smartrecruiters.com/{token}/{postingId}` or
`https://{custom}.mysmartrecruiters.com/.../{postingId}`
→ token from board config, postingId from URL.

### BITE (b-ite.com)

| Aspect | Detail |
|--------|--------|
| List API | `POST https://jobs.b-ite.com/api/v1/postings/search` (JSON body with key, channel, locale, page) |
| Pagination | Offset-based (100/page) |
| Detail API | `GET https://jobs.b-ite.com/jobposting/{hash}/json?locale={locale}&contentRendered=true` |
| Detail fields | title, content.html.rendered (description), address (location), employmentType array, baseSalary |
| URL → detail key | Extract hash from URL path |
| Auth | **API key required** (extracted from widget JS at `cs-assets.b-ite.com/.../*.min.js`) |
| Complexity | Medium — API key extraction needed for list, but detail endpoint uses hash (no key) |

**Note**: The list endpoint requires the API key, but detail endpoint uses the
hash directly. Scraper only needs hash extraction from URL.

### Softgarden

| Aspect | Detail |
|--------|--------|
| List API | `GET https://{slug}.softgarden.io/` (HTML page, job IDs in inline JS `complete_job_id_list`) |
| Pagination | None (single page) |
| Detail API | `GET https://{slug}.softgarden.io/job/{id}?l=en` (HTML page with JSON-LD) |
| Detail fields | JSON-LD JobPosting: title, description, jobLocation, baseSalary, employmentType, datePosted |
| URL → detail key | Extract job ID from URL path |
| Auth | None (public web pages) |
| Complexity | Medium — detail is HTML scraping (JSON-LD extraction), not a clean API |

**Note**: Detail endpoint returns HTML, not JSON. Scraper must parse JSON-LD
from the page. Consider whether existing `jsonld` scraper can handle this
instead of a dedicated softgarden scraper. If JSON-LD extraction is sufficient,
the monitor conversion may only require changing to `set[str]` return + using
the existing `jsonld` scraper type.

### Rippling

| Aspect | Detail |
|--------|--------|
| List API | `GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs` |
| Pagination | None (all jobs in single response) |
| Detail API | `GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}` |
| Detail fields | description (company + role parts), employmentType, payRangeDetails, createdOn, workLocations |
| URL → detail key | Extract UUID from URL path |
| Auth | None (public API) |
| Complexity | Low — clean REST API, single list call, straightforward detail |

**URL format**: `https://{slug}.rippling.com/.../{uuid}` or embedded in list response.
→ slug from board config, uuid from URL.

### Breezy HR

| Aspect | Detail |
|--------|--------|
| List API | `GET https://{portal_url}/json` |
| Pagination | None (all openings in single response) |
| Detail API | Job URL from list (HTML page with JSON-LD and/or description div) |
| Detail fields | JSON-LD: description, locations, employmentType, jobLocationType, datePosted, baseSalary |
| URL → detail key | Full URL from list response |
| Auth | None (public) |
| Complexity | Medium — detail is HTML scraping with custom parser; list already provides some metadata |

**Note**: List provides structured metadata (title, type, locations, salary text).
Detail adds description and JSON-LD overrides. Consider whether list metadata
is sufficient to skip the scraper entirely (make it a rich monitor) or whether
description is essential.

### JOIN (join.com)

| Aspect | Detail |
|--------|--------|
| List API | `GET https://join.com/companies/{slug}/?page=N` (Next.js `__NEXT_DATA__`) |
| Pagination | Page-based (5 jobs/page, `pageCount` from response) |
| Detail API | Job detail page `__NEXT_DATA__` (description from configurable paths) |
| Detail fields | description (schemaDescription → unifiedDescription → description fallback) |
| URL → detail key | idParam from list data |
| Auth | None (public) |
| Complexity | High — Next.js data extraction, Playwright fallback, description from 3 fallback paths |

**Note**: This monitor delegates to the `nextdata` monitor internally. The list
already provides title, location, employmentType, salary, etc. Only description
comes from detail pages. Consider whether a `nextdata` scraper could handle
the detail fetching generically.

---

## Conversion Priority

Ordered by impact (hourly request reduction) and complexity:

| Priority | Monitor | Hourly Detail Calls (typical) | Conversion Complexity | Notes |
|----------|---------|-------------------------------|----------------------|-------|
| 1 | workable | 50–500 | Low | Clean APIs, no auth |
| 2 | smartrecruiters | 50–500 | Low | Clean APIs, retry logic to preserve |
| 3 | rippling | 10–100 | Low | Clean API, single list call |
| 4 | bite | 20–200 | Medium | API key for list only, detail is keyless |
| 5 | breezy | 10–100 | Medium | HTML detail parsing, list has partial data |
| 6 | softgarden | 10–100 | Medium | HTML/JSON-LD detail, may reuse jsonld scraper |
| 7 | join | 5–50 | High | Next.js extraction, Playwright fallback |

---

## Progress Tracker

All N+1 monitors have been converted. No N+1 monitors remain.

| Monitor | Strip detail from monitor | Create scraper | Register scraper | Wire auto-config | Update help cards | Write tests | Backfill DB | Done |
|---------|:------------------------:|:--------------:|:----------------:|:----------------:|:-----------------:|:-----------:|:-----------:|:----:|
| workday | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: |
| workable | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: |
| smartrecruiters | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: |
| rippling | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: |
| bite | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: |
| breezy | :white_check_mark: | :white_check_mark: (auto json-ld+dom) | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: |
| softgarden | :white_check_mark: | :white_check_mark: (auto json-ld) | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: |
| join | :white_check_mark: | :white_check_mark: (auto nextdata) | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: |

Legend: :white_check_mark: done
