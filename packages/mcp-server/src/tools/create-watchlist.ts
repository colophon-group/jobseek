import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";
import {
  SEARCH_EMPLOYMENT_TYPE_LIST_PATTERN,
  SEARCH_EMPLOYMENT_TYPE_VALUES,
  SEARCH_WORK_MODE_LIST_PATTERN,
  SEARCH_WORK_MODE_VALUES,
} from "../public-api-contract.js";
import { apiLocaleSchema } from "../locale-schema.js";

export function register(server: McpServer, client: JobseekClient) {
  server.tool(
    "create_watchlist_link",
    "Generate a prefilled link for the user to create a watchlist on jseek.co. The link opens the watchlist creation page with filters pre-filled. The user must log in to save. Returns a preview with matching job and company counts so you can verify the filters are useful before sharing the link.",
    {
      title: z.string().describe("Watchlist title"),
      description: z.string().optional().describe("Watchlist description"),
      q: z.string().optional().describe("Keywords"),
      loc: z
        .string()
        .optional()
        .describe("Location slugs, comma-separated (from resolve_slugs)"),
      occ: z
        .string()
        .optional()
        .describe("Occupation slugs, comma-separated"),
      sen: z.string().optional().describe("Seniority slugs, comma-separated"),
      tech: z
        .string()
        .optional()
        .describe("Technology slugs, comma-separated"),
      wm: z
        .string()
        .regex(new RegExp(SEARCH_WORK_MODE_LIST_PATTERN))
        .optional()
        .describe(`Work mode, comma-separated: ${SEARCH_WORK_MODE_VALUES.join(", ")}`),
      etype: z
        .string()
        .regex(new RegExp(SEARCH_EMPLOYMENT_TYPE_LIST_PATTERN))
        .optional()
        .describe(
          `Employment type, comma-separated: ${SEARCH_EMPLOYMENT_TYPE_VALUES.join(", ")}`,
        ),
      sal: z.string().optional().describe("Salary range, format: min-max"),
      salcur: z.string().optional().describe("Salary currency code (e.g. EUR, USD, CHF)"),
      exp: z
        .string()
        .optional()
        .describe("Experience range in years, format: min-max"),
      companies: z
        .string()
        .optional()
        .describe("Company slugs, comma-separated"),
      locale: apiLocaleSchema,
    },
    { title: "Create Watchlist Link", readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    async (params) => {
      const data = await client.get("/api/v1/watchlist/create", {
        title: params.title,
        description: params.description,
        q: params.q,
        loc: params.loc,
        occ: params.occ,
        sen: params.sen,
        tech: params.tech,
        wm: params.wm,
        etype: params.etype,
        sal: params.sal,
        salcur: params.salcur,
        exp: params.exp,
        companies: params.companies,
        locale: params.locale,
      });
      return {
        content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
      };
    },
  );
}
