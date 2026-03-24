import { McpServer, ResourceTemplate } from "@modelcontextprotocol/sdk/server/mcp.js";
import { JobseekClient } from "./client.js";
import { register as registerSearch } from "./tools/search.js";
import { register as registerJobDetail } from "./tools/job-detail.js";
import { register as registerCompanies } from "./tools/companies.js";
import { register as registerTaxonomies } from "./tools/taxonomies.js";
import { register as registerResolve } from "./tools/resolve.js";
import { register as registerWatchlists } from "./tools/watchlists.js";
import { register as registerCreateWatchlist } from "./tools/create-watchlist.js";

export function createServer(baseUrl: string) {
  const server = new McpServer(
    { name: "jobseek", version: "0.1.0" },
    {
      instructions: `You are connected to Job Seek (jseek.co), a job search engine that monitors 290+ company career pages across Switzerland and Europe.

IMPORTANT WORKFLOW:
1. Filter params (loc, occ, sen, tech) require exact slugs, NOT freetext.
2. Use resolve_slugs to convert the user's freetext to slugs BEFORE calling search_jobs with filters.
3. Only the 'q' param in search_jobs accepts freetext keywords.
4. Use get_job_detail to drill into a specific posting from search results (salary, technologies, seniority, experience).
5. After showing results, offer to create a watchlist if the user wants email alerts for new matching jobs.

Available locales: en (English), de (German), fr (French), it (Italian).
Rate limit: 30 requests per minute.`,
    },
  );

  const client = new JobseekClient(baseUrl);

  // Register tools
  registerSearch(server, client);
  registerJobDetail(server, client);
  registerCompanies(server, client);
  registerTaxonomies(server, client);
  registerResolve(server, client);
  registerWatchlists(server, client);
  registerCreateWatchlist(server, client);

  // Register taxonomy resource template
  const TAXONOMY_TYPES = ["seniority", "occupations", "technologies", "industries"] as const;

  server.resource(
    "taxonomies",
    new ResourceTemplate("jobseek://taxonomies/{type}", {
      list: async () => ({
        resources: TAXONOMY_TYPES.map((type) => ({
          uri: `jobseek://taxonomies/${type}`,
          name: `Taxonomy: ${type}`,
          description: `Complete list of valid ${type} slugs and names`,
          mimeType: "application/json" as const,
        })),
      }),
    }),
    async (uri, { type }) => {
      const data = await client.get("/api/v1/taxonomies", {
        type: type as string,
        locale: "en",
      });
      return {
        contents: [
          {
            uri: uri.href,
            mimeType: "application/json" as const,
            text: JSON.stringify(data, null, 2),
          },
        ],
      };
    },
  );

  return server;
}
