import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";
import { apiLocaleSchema } from "../locale-schema.js";

export function register(server: McpServer, client: JobseekClient) {
  server.tool(
    "resolve_slugs",
    "Find matching locations, occupations, seniority levels, technologies or industries from freetext and return their current slugs and names. Use it before loc/occ/sen/tech filters when you do not already have a slug returned by Job Seek. Pick from returned matches; clarify ambiguity and do not invent slugs. Industries are discovery information, not a search_jobs filter.",
    {
      type: z
        .enum(["locations", "occupations", "seniority", "technologies", "industries"])
        .describe("Which taxonomy to search"),
      q: z.string().trim().min(2).describe("Freetext query (min 2 chars)"),
      locale: apiLocaleSchema,
    },
    { title: "Resolve Slugs", readOnlyHint: true, destructiveHint: false, openWorldHint: true, idempotentHint: true },
    async (params) => {
      const data = await client.get("/api/v1/resolve", {
        type: params.type,
        q: params.q,
        locale: params.locale,
      });
      return {
        content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
      };
    },
  );
}
