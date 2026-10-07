import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";
import { apiLocaleSchema } from "../locale-schema.js";

export function register(server: McpServer, client: JobseekClient) {
  server.tool(
    "list_taxonomies",
    "Discover Job Seek's available seniority, occupation and technology filter values, or industry suggestions. Seniority items contain slugs/names; occupations and technologies are grouped; industry suggestions contain IDs/names and are not a search_jobs filter. Use resolve_slugs for locations and specific freetext matches. The same English data is available as jobseek://taxonomies/{type} resources.",
    {
      type: z
        .enum(["seniority", "occupations", "technologies", "industries"])
        .describe("Which taxonomy to list"),
      locale: apiLocaleSchema,
    },
    { title: "List Taxonomies", readOnlyHint: true, destructiveHint: false, openWorldHint: true, idempotentHint: true },
    async (params) => {
      const data = await client.get("/api/v1/taxonomies", {
        type: params.type,
        locale: params.locale,
      });
      return {
        content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
      };
    },
  );
}
