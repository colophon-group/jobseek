import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";
import { apiLocaleSchema } from "../locale-schema.js";

export function register(server: McpServer, client: JobseekClient) {
  server.tool(
    "search_watchlists",
    "Search public watchlists on jseek.co. Without a query, returns the most popular watchlists. Watchlists are curated collections of company filters that users share.",
    {
      q: z
        .string()
        .optional()
        .describe("Search query for watchlist title/description"),
      locale: apiLocaleSchema,
    },
    { title: "Search Watchlists", readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    async (params) => {
      const data = await client.get("/api/v1/watchlists", {
        q: params.q,
        locale: params.locale,
      });
      return {
        content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
      };
    },
  );
}
