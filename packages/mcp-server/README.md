# @jseek/mcp-server

MCP server for [Job Seek](https://jseek.co) — search jobs, companies, and watchlists directly from Claude, Cursor, or any MCP-compatible client.

## Installation

### Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "jobseek": {
      "command": "npx",
      "args": ["@jseek/mcp-server"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add jobseek -- npx @jseek/mcp-server
```

### Cursor

Add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "jobseek": {
      "command": "npx",
      "args": ["@jseek/mcp-server"]
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `search_jobs` | Search job postings with filters (keywords, location, seniority, tech stack, salary, experience) |
| `get_job_detail` | Get full metadata for a posting (salary, technologies, seniority, experience, locations) |
| `search_companies` | Search companies by name |
| `list_taxonomies` | List valid filter values (seniority levels, occupations, technologies, industries) |
| `resolve_slugs` | Convert freetext to exact slugs needed for filter params |
| `search_watchlists` | Search public watchlists |
| `create_watchlist_link` | Generate a prefilled watchlist creation link |

## Usage

Ask Claude things like:

- "Find senior backend engineer jobs in Zurich"
- "What companies are hiring for machine learning roles?"
- "Show me React jobs paying over 100k EUR"
- "Create a watchlist for Go developer positions in Switzerland"

The server will guide Claude through the correct workflow: resolving freetext to slugs, searching with those slugs, and drilling into individual postings.

## Options

```
--base-url <url>    API base URL (default: https://jseek.co)
```

## Rate Limits

The Job Seek API is rate-limited to 30 requests per minute per IP.

## License

MIT
