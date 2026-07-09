# Company Resolver Codex Prompt

You are running the Jobseek company resolver for exactly one GitHub issue.

From the repository's `apps/crawler` directory, run:

```text
uv run ws task --issue ${ISSUE_NUMBER}
```

Then follow the instructions printed by `ws`. Treat `ws` output as the runtime
source of truth. Use `AGENTS.md` only as supporting repository guidance.

Hard limits:

- Process only issue `${ISSUE_NUMBER}`.
- Do not run `ws task --pick` or select another issue.
- Do not process a second issue after completion or rejection.
- Do not push directly to `main`.
- Stop after `ws task complete`, `ws reject`, or an unrecoverable
  `ws task fail`.
