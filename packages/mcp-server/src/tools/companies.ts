import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";
import { apiLocaleSchema } from "../locale-schema.js";

export function register(server: McpServer, client: JobseekClient) {
  server.tool(
    "search_companies",
    "Search companies by name on jseek.co. Returns up to 10 matching companies with links to their company pages.",
    { q: z.string().describe("Company name query (min 2 chars)"), locale: apiLocaleSchema },
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
