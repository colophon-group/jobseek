# Auto-Merge Rules

PRs that add company configs qualify for automatic merge based on estimated crawl cost. Other changes need
explicit task/operator merge authorization; approving reviews are not required.

## Policy

| Condition | Label | Policy |
|-----------|-------|--------|
| Low volume (<500 jobs), fast crawl, config-only changes | `auto-merge` | Auto-merge after CI passes |
| Medium volume (500–5000 jobs) | `review-size` | Separate merge authorization |
| High estimated crawl time or resource cost | `review-load` | Separate merge authorization |
| Agent proposes source code changes | `review-code` | Separate merge authorization |

## How It Works

1. The agent estimates job count and crawl time during the test crawl step
2. The agent includes these estimates in the PR body
3. The agent applies the appropriate label based on the thresholds
4. The `maybe-auto-merge.yml` workflow wakes on PR updates and CI/CodeQL
   completion
5. If CI passes and the PR only touches allowed company-request files, it
   merges automatically

## Examples

**Auto-merge** (low risk):
- Sitemap board with 50 jobs → `auto-merge`
- Greenhouse board with 200 jobs → `auto-merge`
- Lever board with 100 jobs → `auto-merge`

**Separate authorization** (medium/high cost or code changes):
- Greenhouse board with 3000 jobs → `review-size`
- DOM-type board requiring Playwright → `review-load`
- Any PR that modifies `.py` files → `review-code`

## Determining Estimates

The agent gets estimates from the test crawl via `ws run monitor`, which reports job count and duration. For sitemap monitors, the URL count from the sitemap is the estimate. For API monitors, the API response includes the total count. For dom monitors, the first page load gives a rough estimate.

## Auto-Merge Workflow

The `maybe-auto-merge.yml` workflow:

1. Wakes on company PR changes, CI/CodeQL completion, a 15 minute schedule,
   and manual dispatch. CI/CodeQL completion on `main` sweeps all eligible
   open same-repo `add-company/*` PRs, so branches made stale by other merges
   do not wait for operator action.
2. Labels internal non-draft `add-company/*` PRs from trusted scripts
3. Skips PRs with pending image files so `upload-company-images.yml` can handle
   the R2 upload path
4. Rebases CSV conflicts when possible and merges via GitHub API
5. Exits cleanly when checks are still pending; the next workflow wake retries
   without operator action

```yaml
on:
  pull_request_target:
    types: [opened, reopened, synchronize, ready_for_review]
  workflow_run:
    workflows: ["CI", "CodeQL"]
    types: [completed]
  schedule:
    - cron: "*/15 * * * *"

jobs:
  auto-merge:
    # ... select eligible company PRs, label, rebase, and merge
```

## Code Change PRs

When an agent proposes source code changes (labeled `review-code`):

- If `ws submit` already published an `add-company/<slug>` draft, it must
  be closed first; code changes discovered before submit have no draft to close
- The new PR must reference the closed draft (e.g. "Supersedes #12") and close the original issue (`Closes #<issue-number>`)
- PR must explain what config options were tried first
- PR must include the failing verification output (test crawl results, extraction samples)
- Code changes should be minimal and focused
- CSV config for the company should be included in the same PR
- Branch naming: `fix-crawler/<description>` instead of `add-company/<slug>`

Code changes are outside the company auto-merger. An explicitly authorized agent or maintainer may merge them after required checks and fresh commit/state validation. Zero approving reviews are required by repository policy.

## External Contributors

Fork PRs are outside the company auto-merger and need separate merge authorization and verification. As the project matures and gains anti-fraud policies (contributor reputation, automated config validation, abuse detection), external PRs may qualify for auto-merge under the same thresholds.

## Safety Rails

- Auto-merge only applies to CSV file changes in `apps/crawler/data/`
- CI must pass (CSV validation, no broken references)
- CodeQL is advisory: its analysis and alerts are available for review but are
  not required branch checks and do not block merge. Data/docs-only PRs may
  skip it. Required checks are `Required CI` and `Crawler Deploy Gate`; the
  checked-in ruleset matches this policy. Making CodeQL blocking would require
  an explicit alert threshold, an always-reported gate and a ruleset change.
- The `auto-merge` label can only be applied by the agent or maintainers
- No mandatory approving review count is enforced, including for source changes.
- Auto-merge validates the exact head/base snapshot, all required checks, draft
  state and hold labels immediately before an atomic expected-head merge.
  A changed snapshot ends that attempt; `review-load` also prevents auto-merge.
