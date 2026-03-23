# Coding Mode

The guided workflow could not complete step **{failed_step}**:
> {fail_reason}

You are now authorized to read source code and propose a fix.

Before writing code, confirm configuration exploration is exhausted:
- For plausible monitor/scraper types, at least one concrete config variant was tried
- Type switching was not done after the first failed/incomplete run unless there was a hard mismatch
- Attempts and failure reasons were recorded
- Evidence trail is clear (observation, method, interpretation)

## Steps

1. **Save workspace state before deleting.** Record the logo/icon selection
   and any enrichment data so you can replay it after the fix:
   ```bash
   ws status          # note logos, descriptions, industry, employee count
   ws logos           # note which candidates were selected
   ```
2. Clean up the failed workspace: `ws del`
3. Create a worktree for the fix (the existing workspace worktree at
   `~/.jobseek/worktrees/<slug>` was removed by `ws del`):
   ```bash
   FIXDIR="$HOME/.jobseek/worktrees/fix-$(openssl rand -hex 4)"
   git -C ~/.jobseek/repo worktree add "$FIXDIR" -b fix-crawler/<description> origin/main
   cd "$FIXDIR/apps/crawler"
   ```
   **Important:** Always randomize the worktree path. Multiple agents may
   run concurrently — a fixed path will cause collisions.
4. Identify the root cause in the source code (`src/core/monitors/`, `src/core/scrapers/`, `src/workspace/`)
5. Make the minimal code change that fixes the issue
6. Include the company's CSV config alongside the code change
7. **Include images.** Download logo and icon into `apps/crawler/data/images/`
   using the URLs you recorded in step 1. The CSV references these files —
   without them the PR is incomplete.
8. Open a PR with:
   - What was tried during the guided workflow
   - Why it failed
   - What the code change does

## Guidelines

- Prefer extending existing monitor/scraper types over adding new ones
- Keep changes minimal — fix the specific issue, don't refactor
- Include tests for new code when feasible
- Label the PR `review-code`
