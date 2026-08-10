# Pre-verify: Is this request valid?

## How this works

You will configure a crawler to monitor a company's career page for job postings.
The `ws` CLI guides you through each step. Run `ws task` at any time to see your
current instructions.

**Rule:** Do **not** explore the codebase or read source code — use `ws` commands
and `ws help` exclusively. All interaction with the system goes through `ws`.

## Issue

**#{issue}**: {issue_title}

{issue_body}

{ats_inventory_context}

---

## Step 1: Check if the company already exists

```bash
ws search "<company name>"
```

An exact company-name match is a **configuration review**, not a reason to
discard the request. Check that the current boards still work, ensure coverage
is complete, and follow every concrete tip or board URL in the issue. Start a
reconfiguration workspace for the existing slug:

```bash
ws new <existing-slug> --issue {issue} --reconfig
```

For a **validated inventory seed**, keep the existing company metadata and use
the seed as evidence for a possible configuration extension. If its exact URL
or ATS tenant is already configured, re-run and compare the current board
instead of rejecting the issue. If it is a new board or tenant, add it to the
reconfiguration workspace for live verification and overlap comparison.

If the company exists only in an open PR and is not on `main` yet, do not create
a competing company PR. Leave this issue pending and retry it after that PR is
resolved; it will then enter the reconfiguration path above.

## Step 2: Verify the company is real and has a careers page

Use web research to confirm:
1. The company exists and is currently operating
2. It has a public-facing careers or jobs page

Research tips:
- Do not assume a specific country or geography unless the issue explicitly says so.
- If the company website is down, check LinkedIn or other sources before rejecting — "website unavailable" is different from "company not found".
- **Search in the company's language**, not just English. Many companies host careers pages in their local language (e.g., "carrières", "Karriere", "carreras", "lavora con noi"). Try `<company> carrières` or `<company> Karriere` early — don't exhaust dozens of English-only searches first.
- **Stop after 5 searches.** If you haven't found a careers page by then, reject with `no-job-board` and note what you tried.

If the company doesn't exist or can't be identified, reject with `not-a-company` or `company-not-found`.
If there's no public careers page and the user cannot provide a URL, reject with `no-job-board`.

**Subsidiary check:** If the company's careers page redirects to a parent
company's centralized portal (e.g. SWISS → Lufthansa Group, Fiat → Stellantis),
configure the **parent company** instead — its portal is the actual data source
and covers all subsidiaries. Reject the issue with `subsidiary` explaining the
situation, then `ws new <parent-slug> --issue {issue}` for the parent.

```bash
ws reject --issue {issue} --reason <key> --message "..."
```

## Step 3: Create the workspace

For a new company, choose a slug (lowercase, hyphens, e.g. `stripe`, `deep-judge`):

```bash
ws new <slug> --issue {issue}
```

Then run `ws task` for the next step.

**Important:** Process only this one issue. After completing or rejecting it, stop — do not pick another issue.
