import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";

const LOCALE = z
  .enum(["en", "de", "fr", "it"])
  .default("en")
  .describe("Response language");

export function register(server: McpServer, client: JobseekClient) {
  server.tool(
    "search_jobs",
    "Search job postings across companies on jseek.co. Returns up to 5 companies with their top 3 matching postings. The 'q' parameter accepts freetext keywords. All filter params (loc, occ, sen, tech) require exact slugs — use resolve_slugs first to convert freetext to slugs.",
    {
      q: z.string().optional().describe("Freetext keywords"),
      loc: z
        .string()
        .optional()
        .describe("Location slugs, comma-separated (from resolve_slugs)"),
      occ: z
        .string()
        .optional()
        .describe("Occupation slugs, comma-separated (from resolve_slugs)"),
      sen: z
        .string()
        .optional()
        .describe("Seniority slugs, comma-separated (from resolve_slugs)"),
      tech: z
        .string()
        .optional()
        .describe("Technology slugs, comma-separated (from resolve_slugs)"),
      sal: z
        .string()
        .optional()
        .describe("Salary range in EUR, format: min-max (e.g. 80000-150000)"),
      exp: z
        .string()
        .optional()
        .describe("Experience range in years, format: min-max (e.g. 3-10)"),
      locale: LOCALE,
    },
    async (params) => {
      const data = await client.get("/api/v1/search", {
        q: params.q,
        loc: params.loc,
        occ: params.occ,
        sen: params.sen,
        tech: params.tech,
        sal: params.sal,
        exp: params.exp,
        locale: params.locale,
      });
      return {
        content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
      };
    },
  );
}
