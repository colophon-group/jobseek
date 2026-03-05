# Step: Validate the Request

Before creating a workspace, verify the request is actionable using web research.
Do **not** use crawler tooling at this stage.

## Pre-check: Already configured?

```bash
grep -q "^{slug}," data/companies.csv
```

If the slug already exists in the first column, comment and close the issue — the company is already configured.

## Check 1 — Real company

Web search confirms the company exists and is operating.

## Check 2 — Public careers page

Find the careers/jobs URL by checking the company's **own website** (look for "Careers" or "Jobs" links).
Do not rely solely on web search results — they may be stale or point to the wrong ATS.
Fetch the company's careers page directly to discover the current board URL.

## Check 3 — At least one listing visible

The career page shows job postings.
**Count the total number of jobs displayed** — you will need this count for `ws probe monitor -n <count>`.

If the page is JS-rendered and WebFetch shows 0 listings, use the job count from web search results
(e.g., LinkedIn, Glassdoor) as an approximation.

Do **not** manually inspect page source, parse `__NEXT_DATA__`, or reverse-engineer API endpoints —
`ws probe monitor` and `ws probe deep` handle this automatically.

## Check 4 — Multiple boards

While on the careers page, look for:
- Language/region switcher (EN | DE | FR tabs)
- Separate URLs per region (`/en/careers`, `/de/careers`, `/us/jobs`)
- Multiple ATS boards (e.g., Greenhouse for engineering + Lever for sales)
- "See jobs in [other country]" links

**Note ALL distinct board URLs found** — these will be configured as separate boards later.
The issue may only reference one URL, but that does not mean it's the only board.

## On failure

If any check fails, reject with the appropriate reason key:

```bash
ws reject --issue {issue} --reason <key> --message "..."
```

Reason keys: `not-a-company`, `company-not-found`, `no-job-board`, `no-open-positions`

## Edge cases

- Ambiguous name with no URL → `company-not-found`
- Careers page behind auth → `no-job-board`
- Unusual format (PDF, iframe) → proceed, monitor/scraper will handle it
- Small company (1–3 jobs) → valid, proceed

## When done

```bash
ws task next --notes "<what you found: company name, board URLs, job counts>"
```
