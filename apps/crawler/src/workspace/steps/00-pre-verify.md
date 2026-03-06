# Pre-verify: Is this request valid?

## How this works

You will configure a crawler to monitor a company's career page for job postings.
The `ws` CLI guides you through each step: verify the request, set up the company,
add career page boards, select and test a monitor/scraper, verify data quality, and submit.

Run `ws task` at any time to see your current step.
If something goes wrong, run `ws task troubleshoot <query>` to search the knowledge base.
If you get stuck on any step, run `ws task fail --reason "..."` to enter coding mode —
this unlocks source code access so you can propose a fix.

**Rule:** Do **not** explore the codebase or read source code — use `ws` commands and `ws help` exclusively.

## Issue

**#{issue}**: {issue_title}

{issue_body}

---

## Already configured?

```bash
grep -q "^<slug>," data/companies.csv
```

If the slug exists, comment and close the issue.

## Is this a real company with a public careers page?

Use web research to confirm:
1. The company exists and is currently operating
2. It has a public-facing careers or jobs page

Do not use crawler tooling at this stage. If the issue URL is missing or ambiguous,
check the company's own website for a "Careers" or "Jobs" link.

If the company doesn't exist or can't be identified, reject with `not-a-company` or `company-not-found`.
If there's no public careers page, reject with `no-job-board`.

```bash
ws reject --issue {issue} --reason <key> --message "..."
```

## If valid — create the workspace

Choose a slug (lowercase, hyphens, e.g. `stripe`, `deep-judge`):

```bash
ws new <slug> --issue {issue}
```

Then run `ws task` for the next step.
