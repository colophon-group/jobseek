# @jseek/mcp-server

MCP server for [Job Seek](https://jseek.co) — search jobs, companies, and watchlists directly from Claude, ChatGPT, Cursor, or any MCP-compatible client.

## Remote Endpoint

A hosted Streamable HTTP endpoint is available at:

```
https://jseek.co/mcp
```

No authentication required. Add it as a custom connector in Claude.ai or ChatGPT.

## Local Installation

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

All tools are annotated as read-only and non-destructive.

## Usage Examples

### Example 1: Find backend jobs in Zurich

**User:** "Find senior backend engineer jobs in Zurich"

The server resolves "Zurich" to the slug `zurich` via `resolve_slugs`, then searches with `search_jobs(q: "backend engineer", loc: "zurich", sen: "senior")`. Returns matching companies with their top postings, each linking to the full listing on jseek.co.

### Example 2: Explore a specific company

**User:** "What jobs does Google have open?"

The server calls `search_companies(q: "Google")` to find the company, then `search_jobs` filtered to that company's postings. For any interesting result, `get_job_detail` returns salary, technologies, seniority, experience requirements, and locations.

### Example 3: Create a watchlist for email alerts

**User:** "Create a watchlist for React jobs in Switzerland paying over 100k EUR"

The server resolves "Switzerland" and "React" to slugs, then calls `create_watchlist_link(title: "React Jobs Switzerland 100k+", loc: "switzerland", tech: "react", sal: "100000-")`. Returns a prefilled link the user can open to save the watchlist and receive email alerts for new matching jobs.

### Example 4: Discover filter options

**User:** "What seniority levels can I filter by?"

The server calls `list_taxonomies(type: "seniority")` and returns all available levels: Intern, Entry Level, Senior, Lead, Staff, Principal, Director, Executive.

## Options

```
--base-url <url>    API base URL (default: https://jseek.co)
```

## Rate Limits

The Job Seek API is rate-limited to 30 requests per minute per IP.

## Privacy Policy

https://jseek.co/en/privacy-policy

## License

MIT
