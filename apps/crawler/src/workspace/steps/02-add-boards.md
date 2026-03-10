# Step: Discover and Add Boards

Find all career page URLs for this company and register each as a board.

Treat discovery output as signals, not directives. For each board candidate,
capture:

1. Observation (counts, links, references)
2. How observed (homepage traversal, rendered page, probe)
3. Likely meaning (primary board, stale board, or uncertain)

## Verify listings exist

The career page must show at least one job posting.
**Count the total number of jobs displayed** — you will need this for `ws probe monitor -n <count>`.

If the page is only a marketing/landing page with a "View jobs" link, do not use the
landing URL as the board. Follow the link and use the actual listings URL (for example
`jobs.lever.co/<company>` or `boards.greenhouse.io/<company>`).

`ws add board` now checks outgoing links on the board URL and tries to infer a
job-link pattern. A real board usually behaves like a **job link hub** (multiple
job-detail links following a consistent pattern). If pattern inference fails, treat
that as a strong signal that the URL may be a marketing page.

`ws` discovery also stores traversal evidence under workspace state. Use this
to distinguish directly referenced boards from blind guesses.

If the page is JS-rendered and shows 0 listings, use the job count from web search results
(e.g., LinkedIn, Glassdoor) as an approximation. If there are genuinely no open positions,
reject with `ws reject --reason no-open-positions --message "..."`.
Careers page behind auth → reject with `ws reject --reason no-job-board --message "..."`.
Small companies with 1–3 jobs are valid — proceed.

Manual source inspection is optional in this phase; start with crawler evidence first.

## Discover board URLs

Starting from the company's careers page, look for:
- Language/region switcher (EN | DE | FR tabs)
- Separate URLs per region (`/en/careers`, `/de/careers`, `/us/jobs`)
- Multiple ATS boards (e.g., Greenhouse for engineering + Lever for sales)
- "See jobs in [other country]" links

The issue URL is a starting point, not a scope constraint.
**The user's country in the issue is where the request came from, not a geographic
filter.** Always use the company's full/global job board URL — never restrict to a
single country or region via query parameters (e.g., `?location=switzerland`).
If the board has a region picker, use the unfiltered base URL so the crawler
captures all listings.
**Note ALL distinct board URLs found.**
Only add URLs that are actual listing boards (or listings feeds), not informational pages.

Prefer directly referenced board URLs over unreferenced slug guesses unless
the latter has stronger corroborating evidence.

## Add each board

```bash
ws add board <alias> --url "<board-url>"
ws add board <alias> --url "<board-url>" --job-link-pattern "<regex>"   # optional override
```

If auto-inference fails after adding, set the pattern manually:

```bash
ws set --board <alias> --job-link-pattern "<regex>"
```

## Pattern safety check (required when setting regex manually)

If you set `--job-link-pattern` manually:

1. Start broad enough to include URL variants (numeric suffixes, trailing slash, query params).
   Prefer optional endings over exact-string matches.
2. Re-run detection and compare against expected site count:
   `ws probe monitor -n <count>` then `ws run monitor`
3. If count drops after adding the pattern, the regex is too strict.
   Widen it before moving to Step 3.
4. Spot-check that each visible posting has a matching URL in monitor output (`jobs.json`).

**Alias naming conventions:**
- Single board: `careers`
- Regional boards: `careers-us`, `careers-de`, `careers-eu`
- Per-ATS boards: `careers-gh` (Greenhouse), `careers-lever`
- Departmental: `careers-engineering`, `careers-sales`

## Multiple boards

```bash
ws add board careers-us --url "https://company.com/us/careers"
ws add board careers-de --url "https://company.com/de/careers"
```

If one board's listings are a strict superset of another's (verified by comparing job counts
and sampling titles), the subset board can be skipped — document this in feedback `--verdict-notes` later.

## When done

```bash
ws task next --notes "<how many boards added, any that were skipped>"
```
