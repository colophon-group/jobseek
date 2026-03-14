# Jobseek

Monitors company career pages for new job postings. Companies are configured via CSV — a Python crawler monitors boards and extracts job details, a Next.js frontend serves the data.

## Contributing: Add a Company

Open issues labeled [`company-request`](https://github.com/colophon-group/jobseek/issues?q=is%3Aopen+label%3Acompany-request) are companies waiting to be added. Each one can be resolved by any coding agent that can run shell commands and access the web.

### Quick start

```
pip install jobseek-crawler-setup
```

Pick an open issue, then hand your agent this prompt:

```
Run `ws task --issue <NUMBER>` and follow the printed instructions.
```

### Requirements

The agent environment needs:
- `git`, `gh` (GitHub CLI, authenticated)
- Python 3.12+
- Web access (to research companies and fetch career pages)

## Supabase (Infra-as-Code)

Supabase project configuration is tracked under `supabase/`, with DB SQL sourced from `apps/web/drizzle/*.sql`.

See `docs/10-supabase-iac.md` for setup and deploy workflow.
