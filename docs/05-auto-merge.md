# Auto-Merge Rules

PRs that add company configs can be auto-merged or require human review, based on estimated crawl cost.

## Policy

| Condition | Label | Policy |
|-----------|-------|--------|
| Low volume (<500 jobs), fast crawl, config-only changes | `auto-merge` | Auto-merge after CI passes |
| Medium volume (500–5000 jobs) | `review-size` | Human review required |
| High estimated crawl time or resource cost | `review-load` | Human review required |
| Agent proposes source code changes | `review-code` | Human review required |

## How It Works

1. The agent estimates job count and crawl time during the test crawl step
2. The agent includes these estimates in the PR body
3. The agent applies the appropriate label based on the thresholds
4. The `auto-merge-config.yml` workflow watches for the `auto-merge` label
5. If CI passes and the PR only touches `data/*.csv`, it merges automatically

## Examples

**Auto-merge** (low risk):
- Sitemap board with 50 jobs → `auto-merge`
- Greenhouse board with 200 jobs → `auto-merge`
- Lever board with 100 jobs → `auto-merge`

**Human review** (medium/high risk):
- Greenhouse board with 3000 jobs → `review-size`
- Discover-type board requiring Playwright → `review-load`
- Any PR that modifies `.py` files → `review-code`

## Determining Estimates

The agent gets estimates from the test crawl:

```bash
# Test crawl reports job count and duration
uv run python -m src.validate --test-monitor stripe https://boards.greenhouse.io/stripe
# Output: Found 247 jobs in 1.2s
```

For sitemap monitors, the URL count from the sitemap is the estimate. For API monitors, the API response includes the total count. For discover monitors, the first page load gives a rough estimate.

## Auto-Merge Workflow

The `auto-merge-config.yml` workflow:

1. Triggers on PRs labeled `auto-merge`
2. Checks that the PR only modifies files in `data/`
3. Waits for CI to pass
4. Enables auto-merge via GitHub API

```yaml
on:
  pull_request:
    types: [labeled]

jobs:
  auto-merge:
    if: github.event.label.name == 'auto-merge'
    # ... verify only data/ files changed, then enable auto-merge
```

## Code Change PRs

When an agent proposes source code changes (labeled `review-code`):

- The original draft PR (`add-company/<slug>`) must be closed first
- The new PR must reference the closed draft (e.g. "Supersedes #12") and close the original issue (`Closes #<issue-number>`)
- PR must explain what config options were tried first
- PR must include the failing verification output (test crawl results, extraction samples)
- Code changes should be minimal and focused
- CSV config for the company should be included in the same PR
- Branch naming: `fix-crawler/<description>` instead of `add-company/<slug>`

Code change PRs never auto-merge — they always require human review, regardless of job count or crawl time.

## External Contributors

PRs from external contributors (forks) currently require human review regardless of size, as a security measure. As the project matures and gains anti-fraud policies (contributor reputation, automated config validation, abuse detection), external PRs may qualify for auto-merge under the same thresholds.

## Safety Rails

- Auto-merge only applies to CSV file changes in `data/`
- CI must pass (CSV validation, no broken references)
- The `auto-merge` label can only be applied by the agent or maintainers
- Any PR touching source code always requires human review
