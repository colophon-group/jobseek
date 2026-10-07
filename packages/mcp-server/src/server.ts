import { McpServer, ResourceTemplate } from "@modelcontextprotocol/sdk/server/mcp.js";
import { JobseekClient, type JobseekClientOptions } from "./client.js";
import { register as registerSearch } from "./tools/search.js";
import { register as registerJobDetail } from "./tools/job-detail.js";
import { register as registerCompanies } from "./tools/companies.js";
import { register as registerTaxonomies } from "./tools/taxonomies.js";
import { register as registerResolve } from "./tools/resolve.js";
import { register as registerCreateWatchlist } from "./tools/create-watchlist.js";
import { register as registerFeedback } from "./tools/feedback.js";
import { MCP_SERVER_INFO, MCP_INSTRUCTIONS } from "./metadata.js";

export function createServer(
  baseUrl: string,
  options: JobseekClientOptions = {},
) {
  const server = new McpServer(MCP_SERVER_INFO, { instructions: MCP_INSTRUCTIONS });

  const client = new JobseekClient(baseUrl, options);

  // Register tools
  registerSearch(server, client);
  registerJobDetail(server, client);
  registerCompanies(server, client);
  registerTaxonomies(server, client);
  registerResolve(server, client);
  registerCreateWatchlist(server, client);
  registerFeedback(server, client);

  // Register taxonomy resource template
  const TAXONOMY_TYPES = ["seniority", "occupations", "technologies", "industries"] as const;

  server.resource(
    "taxonomies",
    new ResourceTemplate("jobseek://taxonomies/{type}", {
      list: async () => ({
        resources: TAXONOMY_TYPES.map((type) => ({
          uri: `jobseek://taxonomies/${type}`,
          name: `Taxonomy: ${type}`,
          description: type === "industries"
            ? "Available industry suggestions with IDs and names; industries are not a search_jobs filter"
            : `Available ${type} values for job filters (occupations and technologies are grouped)`,
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
