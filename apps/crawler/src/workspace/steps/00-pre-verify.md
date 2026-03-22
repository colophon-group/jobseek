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

---

## Step 1: Check if the company already exists

```bash
ws search "<company name>"
```

If found, reject with `duplicate`:

```bash
ws reject --issue {issue} --reason duplicate --message "Already configured as <slug>"
```

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

```bash
ws reject --issue {issue} --reason <key> --message "..."
```

## Step 3: Create the workspace

Choose a slug (lowercase, hyphens, e.g. `stripe`, `deep-judge`):

```bash
ws new <slug> --issue {issue}
```

Then run `ws task` for the next step.

**Important:** Process only this one issue. After completing or rejecting it, stop — do not pick another issue.
