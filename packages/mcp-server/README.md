# @jseek/mcp-server

[Job Seek](https://jseek.co) MCP server for jobs sourced directly from company career pages across a worldwide catalogue. Find roles, compare available salary and experience metadata, discover companies and filter values, prepare email-alert watchlist links, and submit product feedback. Coverage and available postings change; query current matches rather than assuming a company is indexed.

## Hosted setup

Add **https://jseek.co/mcp** to a client supporting Streamable HTTP. No API key is required. Tool approval and connector availability depend on the client.

For [Claude Code](https://code.claude.com/docs/en/mcp):

```bash
claude mcp add --transport http jobseek https://jseek.co/mcp
```

For [Cursor](https://cursor.com/docs/context/mcp), add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "jobseek": { "url": "https://jseek.co/mcp" }
  }
}
```

The hosted endpoint streams POST replies. Standalone GET SSE streams return `405 Method Not Allowed`; each request has a separate stateless transport, without background notifications or stream resumption.

## Local stdio setup

Node.js 18 or later is required. Run `npx -y @jseek/mcp-server`, or configure a client to start it:

```json
{
  "mcpServers": {
    "jobseek": {
      "command": "npx",
      "args": ["-y", "@jseek/mcp-server"]
    }
  }
}
```

This configuration can be used in Claude Desktop's `claude_desktop_config.json` or Cursor's `.cursor/mcp.json`. Claude Code also supports:

```bash
claude mcp add jobseek -- npx -y @jseek/mcp-server
```

Use `--base-url <url>` to target a compatible Job Seek API (default `https://jseek.co`).

## Tools

| Tool | Use it for |
|------|------------|
| `search_jobs` | Job discovery by keyword, location, occupation, seniority, technology, work mode, employment type, EUR salary, experience and document language. Returns up to 5 companies × 3 posting summaries, `totalCompanies`, and a `moreAt` browsing link. |
| `get_job_detail` | Inspect a returned posting ID: title, company, locations, seniority, technologies, salary with original currency/period, experience, employment type, first-seen date and posting URL. |
| `search_companies` | Find up to 10 company-name matches, their slugs and company-page links. |
| `list_taxonomies` | Discover seniority values, grouped occupations/technologies, or industry suggestions with IDs/names. Industries are not a job-search filter. |
| `resolve_slugs` | Resolve freetext locations, occupations, seniority, technologies or industries to current slugs and names. Clarify ambiguous matches. |
| `create_watchlist_link` | Prepare a watchlist link with filters/company prefill and a matching preview. The user opens it and signs in to save. |
| `report_bug` | Submit the task goal, expected/observed behavior, impact and optional sanitized reproduction/workaround. |
| `suggest_feature` | Submit the goal, missing capability, expected benefit, impact and optional workaround. |
| `give_feedback` | Submit a task-based usefulness/usability observation and optional improvement. |

The six discovery/search/link tools are read-only and non-destructive. The three feedback tools write submissions and are non-destructive **but not idempotent**: repeated calls create repeated submissions. They return only basic success/failure, without tracking IDs, deduplication or status retrieval. Feedback is optional; omit personal information, credentials, private URLs, raw arguments and conversation transcripts. Do not retry automatically if confirmation fails.

## Search workflow and limits

1. Resolve unknown `loc`, `occ`, `sen`, `tech` slugs with `resolve_slugs`, or select values returned by `list_taxonomies`. Reuse verified slugs; do not invent them. Locations are resolved individually rather than listed exhaustively.
2. Call `search_jobs` using returned slugs. Only `q` accepts freetext. `wm` accepts `onsite`, `hybrid`, `remote`; `etype` accepts `full_time`, `part_time`, `contract`, `temporary`, `volunteer`. For internships, resolve the Intern seniority level.
3. `sal` is an integer **EUR** min-max range (`80000-150000`, `100000-`, `-80000`). `exp` is the same range shape in years. `lang` accepts comma-separated two-letter posting language codes; omitted means all document languages. Response/link `locale` is independently `en`, `de`, `fr`, or `it` (default `en`).
4. Inspect interesting posting IDs with `get_job_detail`. Missing metadata is unknown. Read the full description and apply through its returned URL.
5. Share `moreAt` for additional browsing: search has no pagination or company filter. For a specific company's full listings, use the page returned by `search_companies`.
6. When the user wants email alerts, prepare a `create_watchlist_link` and let them save it on the website.

For example, to find senior backend roles in Zurich, resolve the location and seniority first, then pass the selected slugs with `q: "backend engineer"`. To follow a particular company, find its current slug with `search_companies` and use it in `create_watchlist_link`'s `companies` prefill.

Watchlist preview caveat: company selection and `salcur` are website prefill. The preview does not apply the company selection, treats salary bounds as EUR, and counts jobs only in its returned company sample. It is not a catalogue-wide posting count.

`jobseek://taxonomies/{type}` resources provide English seniority, occupation, technology and industry data; the `list_taxonomies` tool accepts a response locale. Job descriptions, personal watchlist reads, application tracking, billing and Narrowed results are website workflows, not additional MCP tools. Ghost-analysis tools and anonymous watchlist discovery are retired. The legacy `GET /api/v1/watchlists` compatibility endpoint returns non-cacheable `410 Gone` through 31 October 2026.

## Feedback API

The tools share `POST /api/v1/feedback`. A bounded JSON submission has `kind: "bug" | "feature" | "feedback"` plus that tool's fields. Optional `affectedTool` uses a registered tool name. Feedback is stored with the server version, consumer category and server timestamp; request IPs are used only for rate limiting and are not stored in feedback. The body is limited to 16 KiB and fields have individual length limits.

Success is `{ "success": true }` after storage succeeds. Validation, rate-limit or storage errors return `{ "success": false, "error": "<code>" }`; responses are not cached. See the [OpenAPI contract](https://jseek.co/api/openapi.json).

Public reads share a pre-cache edge budget of 60 requests/minute per IP with public-read website actions, plus a 30/minute origin limit. The edge may return `403`, the origin `429`. Feedback has a separate 5 submissions/hour client-IP budget. Respect rate limits and retry instructions. Hosted deployments use the existing protected provenance token to forward the authoritative client IP for feedback limiting; without it the backend sees the shared host IP.

## Publishing and deployment

Releases publish from `.github/workflows/publish-mcp-server.yml` with npm trusted publishing (GitHub OIDC, no long-lived `NPM_TOKEN`). The trusted publisher must identify `colophon-group/jobseek`, workflow `publish-mcp-server.yml`, environment `production`, action `npm publish`.

Keep `package.json`, `server.json` (including its package entry), and `MCP_SERVER_VERSION` identical. A main-branch push publishes only when the version differs from npm; lookup failures stop the release. Registry metadata advertises both npm stdio and the hosted Streamable HTTP endpoint.

The feedback table migration must be applied through the reviewed routine web-migration workflow before feedback submissions can succeed. No feedback UI, triage, tracking or deduplication is included.

## Privacy and license

[Privacy policy](https://jseek.co/en/privacy-policy). Application code: MIT; job data: CC BY-NC 4.0. See [data licensing](https://jseek.co/en/license).
