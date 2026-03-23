# Track C: Board Discovery

Workspace: `{{ slug }}`
Website: {{ website }}
{% if company_name %}Company: {{ company_name }}{% endif %}


## Goal

Find **all** career boards for the company and register them. The main
agent will start processing boards as you add them — work progressively,
adding boards as you discover them rather than waiting to find all of them.

> **Scope is global, not locale-specific.** The user's country in the
> GitHub issue is where the request came from, **not a geographic filter**.
> Find and register ALL of the company's career boards worldwide. Never
> restrict to a single country or region. Never add query parameters like
> `?location=switzerland` to board URLs — use the unfiltered base URL.

## How to add a board

```bash
ws add board {{ slug }} <alias> --url "<board-url>"
```

Alias convention: `careers`, `careers-de`, `careers-uk`,
`careers-engineering`, `careers-workday`, etc.

The board URL must be the **actual listing source** (ATS board, job feed),
not a marketing careers landing page. If the page shows a list of job
postings with links to individual jobs, it's a board.

## Discovery checklist

Background discovery may have found career page candidates. Check
`ws status` to see what has been discovered so far.

### 1. Check the main careers page

Visit the company website and find their careers/jobs page. Look for:
- Direct job listings (this IS the board URL)
- An embedded ATS widget (Greenhouse, Lever, Workday, etc.)
- A redirect to an external ATS domain

### 2. Check for regional variants

Look for hreflang links on the careers page. If discovery found hreflang
variants, you can batch-create boards from them:

```bash
ws add boards {{ slug }}  # batch-creates boards from hreflang discovery
```

**`ws add boards` (plural) is ONLY for hreflang batch import.** For all
other boards, use `ws add board` (singular) with an alias and URL.

Also search for regional career domains manually:
- `{{ website }}/careers`, `{{ website }}/jobs`
- Country-specific sites: `company.de/karriere`, `company.fr/carrieres`

### 3. Check for multiple ATS platforms

Large companies often use different ATS systems for different departments:
- Greenhouse for engineering
- Lever for sales/marketing
- Workday for corporate/finance
- SuccessFactors for regional offices

Search the company name + "jobs" + common ATS domains.

### 4. Verify before adding

**Add only confirmed boards — do not add speculatively.** For each URL:
- Verify it belongs to the target company (not a subsidiary, partner, or
  different company with a similar name)
- Verify it shows actual job listings (not "coming soon" or an empty page)
- Verify it's distinct from other boards (not a mirror/subset of another
  board — e.g., WTTJ often mirrors Ashby listings)
- Do NOT read source code files — use `ws help` and `ws task troubleshoot` instead

{% if monitor_table %}
### Auto-detected ATS types

{{ monitor_table }}
{% else %}
Run `ws help monitors` for the list of auto-detected ATS types.
{% endif %}

### 5. Verify completeness for large companies

**Multinational companies (500+ employees, offices in multiple countries)
almost certainly have multiple boards.** If you have found only 1 board for
such a company, your discovery is likely incomplete. Go back and check:
- Region/language switchers on the careers page
- `robots.txt` sitemap entries mentioning careers paths
- Hreflang tags for regional variants
- Multiple ATS domains (different ATS for different departments)

Do not signal completion with only 1 board unless you have strong evidence
the company genuinely uses a single centralized board.

## Signal completion — MANDATORY

When you have finished discovering all boards, you **MUST** signal
completion so the main agent stops waiting:

```bash
ws boards-done {{ slug }}
```

**This is your final action.** The main agent blocks on `ws await-board`
until you signal done. Forgetting this will stall the entire pipeline.

## Report

After discovery, state what you found: total board count, which regions
or departments are covered, and any boards you couldn't verify.
