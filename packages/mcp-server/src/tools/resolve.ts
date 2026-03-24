import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";

export function register(server: McpServer, client: JobseekClient) {
  server.tool(
    "resolve_slugs",
    "Convert freetext to exact taxonomy slugs needed for filter parameters. ALWAYS call this before using loc/occ/sen/tech params in search_jobs. For example, resolve 'Zurich' to get the slug 'zurich', or 'machine learning' to get 'machine-learning'.",
    {
      type: z
        .enum(["locations", "occupations", "seniority", "technologies", "industries"])
        .describe("Which taxonomy to search"),
      q: z.string().describe("Freetext query (min 2 chars)"),
      locale: z
        .enum(["en", "de", "fr", "it"])
        .default("en")
        .describe("Response language"),
    },
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
