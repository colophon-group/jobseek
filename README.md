# Jobseek

Monitors company career pages for new job postings. Companies are configured via CSV — a Python crawler monitors boards and extracts job details, a Next.js frontend serves the data.

## Contributing: Add a Company

Open issues labeled [`company-request`](https://github.com/colophon-group/jobseek/issues?q=is%3Aopen+label%3Acompany-request) are companies waiting to be added. Each one can be resolved by any coding agent that can run shell commands and access the web.

### Quick start

Pick an open issue, then hand your agent this prompt:

```
Clone https://github.com/colophon-group/jobseek.git and resolve
issue #<NUMBER> by following the instructions in AGENTS.md.
```

The agent will research the company, detect the right monitor type, test-crawl the board, add CSV rows, validate, and open a PR.

### Requirements

The agent environment needs:
- `git`, `gh` (GitHub CLI, authenticated)
- Python 3.12+ with [`uv`](https://docs.astral.sh/uv/)
- Web access (to research companies and fetch career pages)

### What the agent does

1. Checks for existing PRs on the issue (avoids duplicate work)
2. Creates a draft PR on branch `add-company/<slug>`
3. Researches the company — name, website, logo, icon, career page
4. Detects the monitor type and test-crawls the board
5. Configures and verifies the scraper
6. Adds rows to `data/companies.csv` and `data/boards.csv`
7. Validates and marks the PR as ready

Full instructions are in [`AGENTS.md`](./AGENTS.md). Architecture docs are in [`docs/`](./docs/).
