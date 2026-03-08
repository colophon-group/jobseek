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

All workspace commands use the `ws` CLI tool (`alias ws='uv run ws'`). See AGENTS.md for the full command reference.

1. **Validate the request**: Before creating any workspace, use web
   research to verify the request is actionable:
   - Check `data/companies.csv` to confirm the company isn't already configured
   - Confirm the company exists and is currently operating
   - Find a public careers page with at least one visible job listing
   - On failure: `ws reject --issue N --reason <key> --message "..."` — this comments with a structured reason marker, closes the issue, and stops
   - See AGENTS.md for the full list of rejection reasons and edge cases

2. **Claim the issue**: `ws new <slug> --issue N` — checks gh auth, checks for
   existing PRs (active → stop, stale → comment and proceed), creates branch,
   seeds stub company row, opens draft PR. Sets the active workspace so all
   subsequent commands auto-resolve the slug.

3. **Set company details**: `ws set --name "..." --website "..." --logo-url "..." --icon-url "..."`
   - Agent researches: official name, homepage, full primary logo URL, and minified square logo/icon URL
   - Use direct image file URLs (not pages containing images), brand-correct assets only
   - Prefer transparent-background assets for both logo and icon when available
   - URL validation is advisory — values are always saved

4. **Add board and probe monitors**: `ws add board <alias> --url <board-url>` then `ws probe`
   - The board URL must be the actual listings source (ATS board/feed), not a marketing careers landing page
   - Probe tries all monitor types and reports results with suggested configs
   - See [04 — Monitors and Scrapers](./04-monitors-and-scrapers.md) for details

5. **Select and test monitor**: `ws select monitor <type> [--as <name>]` then `ws run monitor`
   - Use `--as <name>` to try multiple configurations under different names
   - Check the career page for a displayed job count (e.g. "Showing 247 open positions")
   - Compare crawled count against the website's count (should be within ~10%)
   - If there's a significant gap, iterate: `ws select monitor <other-type> --as <name>`, `ws run monitor`
   - Use `ws select config <name>` to switch back to a previously tested config
   - If 0 jobs returned but validation passed in step 1, it's a monitor misconfiguration — debug systematically
   - See "Verification and Iteration" below for details

6. **Select and test scraper** (non-API monitors only): `ws select scraper <type>` then `ws run scraper`
   - API monitors (greenhouse, lever, ashby, etc.) return full data and auto-skip this step
   - Verify extraction on 2–3 sample job URLs (title, location, description)
   - If extraction fails, iterate: revise config or try a different scraper type

7. **Record feedback**: `ws feedback --title clean --description clean --verdict good`
   - Mandatory before submit — quality gates enforce this
   - `description` is expected to be HTML; markup alone is not a quality issue
   - Verdict options: `good`, `acceptable`, `poor` (submit with `--force`), `unusable` (cannot submit)
   - If verdict is `poor` or `unusable`: `ws reject-config <name> --reason "..."` and try another config

8. **Escalate to code changes** (if needed): When no existing config works:
   - Record feedback with `--verdict unusable` to document what failed
   - `ws del` to clean up the config-only workspace
   - Create a new PR on a `fix-crawler/<description>` branch with `review-code` label
   - Reference the closed draft PR and ensure the new PR closes the original issue
   - See "Escalating to Code Changes" below for details

9. **Submit**: `ws submit [--summary "..."] [--force]` — runs quality gates, writes CSV,
   validates, commits, pushes, posts crawl stats + transcript on PR, marks PR ready, posts
   completion on issue. Submit is checkpoint-based — if it fails partway, use `ws resume` to
   diagnose and re-run. Then advance task state with `ws task next --notes "..."`
   (and later `ws task complete`). CI handles labeling and merging (see [05 — Auto-Merge](./05-auto-merge.md)).

## Verification and Iteration

Agents should not blindly trust the first test crawl result. The workflow includes verification loops to catch incomplete configurations early.

**Monitor verification**: After `ws run monitor`, cross-reference the crawled job count with what the career page displays. Many sites show a total like "247 open positions" — if the monitor only found 50, something is wrong. Common causes:
- The sitemap doesn't include all job URLs → try `dom` or `nextdata` monitor instead
- The API token is wrong or returns a different subset → try alternative API slugs
- Pagination isn't working → check pagination config
- The monitor type is wrong entirely → re-run `ws probe` or try manually

**Scraper verification**: After `ws run scraper`, check the extraction quality table. Verify that title, location, and description extract correctly. If fields are empty or garbled:
- CSS selectors may be wrong → inspect the page HTML, try different selectors
- JSON-LD may be partial → switch from `json-ld` to `html` scraper
- Page may need JS to render → switch from `html` to `dom` scraper

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

**PR transition workflow**: Since the agent already created a workspace and draft PR, it must clean up before creating the code change PR:
1. Run `ws del` — this closes the draft PR, removes CSV rows, and deletes the branch
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
- Scraper: <type> (e.g., json-ld / dom / empty for API monitors)
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
