# Step: Discover and Add Boards

Find all career page URLs for this company and register each as a board.

## Verify listings exist

The career page must show at least one job posting.
**Count the total number of jobs displayed** — you will need this for `ws probe monitor -n <count>`.

If the page is JS-rendered and shows 0 listings, use the job count from web search results
(e.g., LinkedIn, Glassdoor) as an approximation. If there are genuinely no open positions,
reject with `ws reject --reason no-open-positions --message "..."`.
Careers page behind auth → reject with `ws reject --reason no-job-board --message "..."`.
Small companies with 1–3 jobs are valid — proceed.

Do **not** manually inspect page source, parse `__NEXT_DATA__`, or reverse-engineer API endpoints —
the crawler tooling handles this automatically.

## Discover board URLs

Starting from the company's careers page, look for:
- Language/region switcher (EN | DE | FR tabs)
- Separate URLs per region (`/en/careers`, `/de/careers`, `/us/jobs`)
- Multiple ATS boards (e.g., Greenhouse for engineering + Lever for sales)
- "See jobs in [other country]" links

The issue URL is a starting point, not a scope constraint.
**Note ALL distinct board URLs found.**

## Add each board

```bash
ws add board <alias> --url "<board-url>"
```

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
