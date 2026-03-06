# Coding Mode

The guided workflow could not complete step **{failed_step}**:
> {fail_reason}

You are now authorized to read source code and propose a fix.

## Steps

1. Clean up the failed workspace: `ws del`
2. Clone the repo into a separate working copy (do NOT use `~/.jobseek/repo/` — that belongs to `ws`):
   ```bash
   git clone https://github.com/colophon-group/jobseek.git /tmp/jobseek-fix
   cd /tmp/jobseek-fix/apps/crawler
   ```
3. Identify the root cause in the source code (`src/core/monitors/`, `src/core/scrapers/`, `src/workspace/`)
4. Create a `fix-crawler/<description>` branch
5. Make the minimal code change that fixes the issue
6. Include the company's CSV config alongside the code change
7. Open a PR with:
   - What was tried during the guided workflow
   - Why it failed
   - What the code change does

## Guidelines

- Prefer extending existing monitor/scraper types over adding new ones
- Keep changes minimal — fix the specific issue, don't refactor
- Include tests for new code when feasible
- Label the PR `review-code`
