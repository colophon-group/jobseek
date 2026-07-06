# Manual Codex Company Resolver Fallback

You are running the manual, API-billed Codex fallback for a Jobseek company-request issue.

Process exactly one issue: the numeric issue in the `WS_FALLBACK_ISSUE` environment variable.

Start from the existing workspace workflow entrypoint:

```bash
cd apps/crawler
uv run ws task --issue "$WS_FALLBACK_ISSUE"
```

Follow the instructions printed by `ws task`. Use the `ws` workflow to pre-verify, create the workspace, configure boards, test monitor/scraper configs, submit, and complete the task. Do not implement a parallel resolver, do not run `ws task --pick`, and do not process any other issue or unbounded backlog.

When the workflow is complete, stop after the single selected issue. Include a concise final summary with the issue number, resulting PR/branch if created, and any blocker or manual follow-up.
