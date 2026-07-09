# Agent Workflow

How coding agents resolve company-request issues.

## Trigger

1. A user submits a company name or URL through the web app
2. The web app creates a GitHub issue with the `company-request` label
3. The issue body contains the user's input (company name, URL, or both)

## Agent Resolution

A coding agent picks up the issue and resolves it by creating a PR. The agent can be:

- **Codex** via the Hetzner local runner, `codex exec --json`, or another
  local subscription-backed Codex session
- **Claude Code** through the legacy compatible route while migration is in
  progress
- **Any AGENTS.md-compatible agent** (Copilot, Cursor, etc.)
- **A human contributor** following the same steps

### Decision style

Treat tool output as evidence, not as a command stream.

For each major decision (board URL, monitor, scraper), capture:

1. What was observed
2. How it was observed
3. What that likely means

Do not lock decisions purely because a probe suggested "Next: ...". Prefer direct site evidence and verify mismatches explicitly.

### Workflow

The agent runs `ws task --issue N` which guides it through the entire flow:

1. **Pre-verify:** `ws search` to check duplicates, web research to confirm
   the company exists with a public careers page
2. **Create workspace:** `ws new <slug> --issue N`
3. **Parallel setup:** `ws task` renders the parallel orchestrator which tells
   the agent to spawn background subagents for enrichment, logos, and board
   discovery. `ws set --website` triggers background auto-discovery.
4. **Progressive board processing:** `ws await-board` blocks until Track C
   adds a board, then the main agent probes and configures it immediately.
   Config testing spawns parallel subagents per monitor+scraper combo.
5. **Submit:** `ws submit` validates, commits, pushes. `ws task complete`
   exports the trace and marks the PR ready.

See [10 — Parallel Agent Pipeline](./10-parallel-agent-pipeline.md) for the
full design, config selection policy, quality enforcement, and subagent
prompt templates.

All ws commands are documented in `apps/crawler/AGENTS.md`.

### Reconfiguration

To reconfigure an existing company (broken scraper, changed site, user report):

```
ws new <slug> --reconfig [--start-at <step>]
```

This pre-loads existing company data and boards from CSV and starts the
workflow at the specified step. Start-at options:

| Step | Use when |
|------|----------|
| `add_boards` | Board URL changed or site fully migrated |
| `select_monitor` | Monitor broke (default) |
| `select_scraper` | Only the scraper broke, monitor still works |
| `verify_and_feedback` | Need to re-verify extraction quality |

Use `ws task back --to <step> --reason "..."` within a reconfig run if you
discover deeper breakage than expected.

## Verification and Iteration

Agents should not blindly trust the first test crawl result. The workflow includes verification loops to catch incomplete configurations early.

Before switching monitor/scraper type, check config references:
- `ws help monitor <type>` / `ws help scraper <type>`
- `ws help fields`, `ws help steps`, `ws help actions`
- `ws help artifacts` (which debug files to inspect)
- `ws help troubleshooting` or `ws task troubleshoot '<symptom>'`

**Monitor verification**: After `ws run monitor`, cross-reference the crawled job count with what the career page displays. Many sites show a total like "247 open positions" — if the monitor only found 50, something is wrong. Common causes:
- The sitemap includes only a subset → first tune `sitemap_url` / `url_filter`, then switch to `dom` or `nextdata` if still incomplete
- The API token/slug is wrong → set it explicitly (e.g. `ws select monitor greenhouse --config '{"token":"<token>"}'`)
- Pagination isn't working → update monitor pagination config and re-run
  - Set `max_pages` to significantly overshoot expected pages to preserve
    completeness; rely on "stop when no new jobs" behavior instead of
    conservative limits
- The monitor type is wrong entirely → re-run `ws probe` and then switch

**Scraper verification**: After `ws run scraper`, check the extraction quality table. Verify that title, location, and description extract correctly. If fields are empty or garbled:
- Field mapping/path may be wrong → inspect artifacts (`sample-*.html`, `sample-*.json`, `flat.json`) and adjust config
- JSON-LD may be partial → try `embedded` or `nextdata`
- Page may need JS to render → enable `render: true`/actions before switching type
- DOM steps may be out of order → inspect `flat.json` and fix step order

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

## Company Resolver Automation

The primary recurring path is the Hetzner-hosted local Codex runner documented
in [18 - Codex Automation Deployment](18-codex-automation-deployment.md). It
checks the backlog every 15-30 minutes, self-regulates against Codex usage and
host capacity, selects at most one open `company-request` issue per accepted
run, claims it with the shared `ws` claim protocol, and runs
`ws task --issue <N>` from `apps/crawler` in an isolated worktree.

The retired GitHub Actions resolvers must not be reintroduced for this
automation. Manual recovery uses the same local Codex CLI path from a
throwaway worktree, with the Hetzner ledger and `ws` claims checked first.
Safety budget: max 5 issues per 5-hour rolling window in conservative mode
unless the Hetzner deployment config deliberately raises it.

The recurring resolver:
1. Selects the oldest open `company-request` issue that has no active PR (or whose PR is stale)
2. Checks host headroom, active claims, active PRs, and Codex usage telemetry
3. Runs the local Codex CLI path to resolve the selected issue
4. Captures `codex exec --json` trace data and local usage into the governor ledger
5. The agent follows the AGENTS.md instructions to create a PR

The recurring resolver is not triggered by GitHub Actions. Use
`codex exec --json` for traceable local runs and keep manual recovery on the
same local Codex path so billing, auth, trace capture, and the governor ledger
remain consistent.

Conflict resolution: if an open PR already claims an issue, the resolver checks
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
