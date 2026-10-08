import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";
import { apiLocaleSchema } from "../locale-schema.js";

export function register(server: McpServer, client: JobseekClient) {
  server.tool(
    "search_companies",
    "Find companies in Job Seek's current catalogue by name. Returns up to 10 matches with names, slugs, icons and company-page URLs. Open a returned company page to browse that company's listings. Company slugs can prefill create_watchlist_link; search_jobs does not accept a company filter.",
    { q: z.string().trim().min(2).describe("Company name query (min 2 chars)"), locale: apiLocaleSchema },
    { title: "Search Companies", readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    async (params) => ({
      content: [{
        type: "text",
        text: JSON.stringify(await client.get("/api/v1/companies", {
          q: params.q,
          locale: params.locale,
        }), null, 2),
      }],
    }),
  );
}
