# Agent Workflow

How coding agents resolve company-request issues.

## Trigger

1. A user submits a company name or URL through the web app
2. The web app creates a GitHub issue with the `company-request` label
3. The issue body contains the user's input (company name, URL, or both)

## Agent Resolution

A coding agent picks up the issue and resolves it by creating a PR. The agent can be:

- **Claude Code** via GitHub Actions (runs hourly, automated)
- **Any AGENTS.md-compatible agent** (Copilot, Codex, Cursor, etc.)
- **A human contributor** following the same steps

### Step-by-Step

1. **Claim**: First, check if an open PR already references this issue
   (`gh pr list --state open --search "Closes #N"`). If an active PR
   exists (recent commits within threshold: 24h for config PRs, 72h for
   code changes) — **stop entirely**. If the PR appears stale, comment
   on it and proceed to create your own. If no PR exists, create a draft
   PR mentioning the issue (`Closes #N`). This signals to other agents
   that the issue is taken.

2. **Research**: Agent finds:
   - Official company name
   - Company homepage URL
   - Logo URL (direct image file, not a page containing a logo)
   - Favicon/icon URL
   - Career/jobs page URL(s) — there may be multiple boards

3. **Detect monitor type**: For each board URL, determine the best monitor:
   - Run `uv run python -m src.validate --detect <url>` to auto-detect
   - Or identify manually: Greenhouse and Lever boards have distinctive URLs
   - See [04 — Monitors and Scrapers](./04-monitors-and-scrapers.md) for details

4. **Test crawl and verify monitor**: Run the test crawl, then verify the results:
   - `uv run python -m src.validate --test-monitor <company-slug> <board-url>`
   - Check the career page for a displayed job count (e.g. "Showing 247 open positions")
   - Compare crawled count against the website's count (should be within ~10%)
   - If there's a significant gap, iterate: try a different monitor type, check pagination config, re-run detection
   - See "Verification and Iteration" below for details

5. **Configure and verify scraper**: For URL-only monitors, determine and test the scraper:
   - Try JSON-LD first (`uv run python -m src.validate --probe-jsonld <url>`)
   - Fall back to HTML selectors if no JSON-LD
   - API monitors (greenhouse, lever) don't need a scraper config
   - Verify extraction on 2–3 sample job URLs (title, location, description)
   - If extraction fails, iterate: revise selectors or try a different scraper type

6. **Escalate to code changes** (if needed): When no existing config works:
   - Close the draft PR from step 1 (the `add-company/<slug>` branch)
   - Create a new PR on a `fix-crawler/<description>` branch with `review-code` label
   - Reference the closed draft PR and ensure the new PR closes the original issue
   - See "Escalating to Code Changes" below for details

7. **Add CSV rows**:
   - Add a row to `data/companies.csv` (company info)
   - Add a row to `data/boards.csv` for each board (monitor + scraper config)
   - Run `uv run python -m src.validate` to check CSV validity

8. **Finalize PR**:
   - Mark PR as ready for review
   - Include in PR body: job count estimate, crawl time, monitor/scraper types
   - Apply appropriate label for auto-merge rules (see [05 — Auto-Merge](./05-auto-merge.md))

## Verification and Iteration

Agents should not blindly trust the first test crawl result. The workflow includes verification loops to catch incomplete configurations early.

**Monitor verification**: After a test crawl, cross-reference the crawled job count with what the career page displays. Many sites show a total like "247 open positions" — if the monitor only found 50, something is wrong. Common causes:
- The sitemap doesn't include all job URLs → try `discover` monitor instead
- The API token is wrong or returns a different subset → try alternative slugs
- Pagination isn't working → check pagination config
- The monitor type is wrong entirely → re-run `--detect` or try manually

**Scraper verification**: After configuring a scraper, test extraction on 2–3 sample job URLs. Check that the title, location, and description extract correctly. If fields are empty or garbled:
- CSS selectors may be wrong → inspect the page HTML, try different selectors
- JSON-LD may be partial → switch from `json-ld` to `html` scraper
- Page may need JS to render → switch from `html` to `browser` scraper

The goal is to iterate until the configuration is verified, not to submit on the first attempt.

## Escalating to Code Changes

When config alone can't handle a site, the agent can propose source code changes. This is a different workflow from a standard company addition:

| Aspect | Config-only PR | Code change PR |
|--------|---------------|----------------|
| Branch name | `add-company/<slug>` | `fix-crawler/<description>` |
| Label | `auto-merge` or `review-size`/`review-load` | `review-code` (always) |
| PR body | Standard format | Must explain what config options were tried first |
| Review | May auto-merge | Always requires human review |

**Before proposing code changes**, the agent must:
1. Exhaust all config options (different monitor types, different scraper types, different selectors)
2. Document what was tried and why it failed
3. Keep the code change minimal and focused

**PR transition workflow**: Since the agent already created a draft PR in step 1, it must clean up before creating the code change PR:
1. Close the original draft PR (`add-company/<slug>`) with a comment explaining why (e.g. "Config-only approach insufficient — escalating to code change")
2. Create a new branch (`fix-crawler/<description>`) and a new PR
3. In the new PR body, reference the closed draft (e.g. "Supersedes #12") so reviewers can see what was tried
4. Ensure the new PR body includes `Closes #<issue-number>` to close the original issue

The CSV config for the company should be included in the same PR alongside the code change.

## GitHub Actions Automation

The `resolve-company-requests.yml` workflow runs hourly:

```yaml
on:
  schedule:
    - cron: "0 */1 * * *"
  workflow_dispatch:
```

Budget: max 5 issues per 5-hour rolling window to control costs.

The workflow:
1. Selects the oldest open `company-request` issue that has no active PR (or whose PR is stale)
2. Checks the budget (how many were processed recently)
3. Runs Claude Code Action to resolve the selected issue
4. The agent follows the AGENTS.md instructions to create a PR

Conflict resolution: if an open PR already claims an issue, the workflow checks
staleness based on the last commit date — 24h threshold for config-only PRs, 72h
for code change PRs (those with the `review-code` label). Stale PRs receive a
one-time warning comment. The issue is only assigned to an agent when no active
claim exists.

## Branch Naming

For config-only company additions:

```
add-company/stripe
add-company/meta
add-company/revolut
```

For code change PRs (when escalating):

```
fix-crawler/custom-api-pagination
fix-crawler/workday-scraper
```

## PR Format

```markdown
## Add <Company Name>

- Website: <url>
- Board: <board_url>
- Monitor: <type> (e.g., greenhouse)
- Scraper: <type> (e.g., greenhouse_api / json-ld / html)
- Estimated jobs: <count>
- Estimated crawl time: <duration>

Closes #<issue-number>
```

## What Agents Must Not Do

- Skip the verification loop (monitor count check + scraper extraction test)
- Submit code changes without first exhausting config options
- Submit a PR with known extraction failures
- Add companies without a valid, working board URL
- Process more than one issue per run
- Push directly to main (always create a PR)

## What Replaces the Old Resolver

The Python resolver (`src/resolver/`) used OpenAI's web search API to find company info and auto-detect board types. This is replaced by:

| Old (resolver) | New (agent) |
|----------------|-------------|
| OpenAI web search | Agent's built-in web browsing |
| AI screening gate | Agent judgment + PR review |
| Automated DB writes | CSV config → DB sync on deploy |
| Status machine (pending/processing/completed) | Issue → PR → merged |
| `company_request` table as state | GitHub issues as state |

The `company_request` table is kept as an audit log but is no longer the source of truth for resolution state.
