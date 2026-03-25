import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";

export function register(server: McpServer, client: JobseekClient) {
  server.tool(
    "list_taxonomies",
    "List all valid values for a taxonomy type (seniority levels, occupations, technologies, or industries). Use this to discover available filter values before searching.",
    {
      type: z
        .enum(["seniority", "occupations", "technologies", "industries"])
        .describe("Which taxonomy to list"),
      locale: z
        .enum(["en", "de", "fr", "it"])
        .default("en")
        .describe("Response language"),
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
