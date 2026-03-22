# Track C: Board Discovery

Workspace: `{{ slug }}`
Website: {{ website }}
{% if company_name %}Company: {{ company_name }}{% endif %}


## Goal

Find **all** career boards for the company and register them. The main
agent will start processing boards as you add them — work progressively,
adding boards as you discover them rather than waiting to find all of them.

## How to add a board

```bash
ws add board <alias> --url "<board-url>"
```

Alias convention: `careers`, `careers-de`, `careers-uk`,
`careers-engineering`, `careers-workday`, etc.

The board URL must be the **actual listing source** (ATS board, job feed),
not a marketing careers landing page. If the page shows a list of job
postings with links to individual jobs, it's a board.

## Discovery checklist

Background discovery may have found career page candidates. These
are stored in the workspace and will be shown when you run
`ws add boards`. Use them as hints to speed up board discovery.

### 1. Check the main careers page

Visit the company website and find their careers/jobs page. Look for:
- Direct job listings (this IS the board URL)
- An embedded ATS widget (Greenhouse, Lever, Workday, etc.)
- A redirect to an external ATS domain

### 2. Check for regional variants

Look for hreflang links on the careers page:

```bash
ws add boards  # batch-creates boards from hreflang discovery
```

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

### 4. Verify each board

For each URL, confirm:
- It shows actual job listings (not "coming soon" or an empty page)
- It's distinct from other boards (not the same jobs on a different URL)

Run `ws help monitors` for the list of auto-detected ATS types.

## Signal completion

When you have finished discovering all boards, signal completion so the
main agent stops waiting:

```bash
ws boards-done
```

This is **required** — the main agent blocks on `ws await-board` until
you signal done. Do this as your very last action.

## Report

After discovery, state what you found: total board count, which regions
or departments are covered, and any boards you couldn't verify.
