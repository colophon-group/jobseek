import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";
import {
  SEARCH_EMPLOYMENT_TYPE_VALUES,
  SEARCH_EMPLOYMENT_TYPE_LIST_PATTERN,
  SEARCH_INTEGER_RANGE_PATTERN,
  SEARCH_LANGUAGE_LIST_PATTERN,
  SEARCH_WORK_MODE_LIST_PATTERN,
  SEARCH_WORK_MODE_VALUES,
} from "../public-api-contract.js";
import { apiLocaleSchema } from "../locale-schema.js";

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
      sal: z
        .string()
        .regex(new RegExp(SEARCH_INTEGER_RANGE_PATTERN))
        .optional()
        .describe("Salary range in EUR, format: min-max (e.g. 80000-150000)"),
      exp: z
        .string()
        .regex(new RegExp(SEARCH_INTEGER_RANGE_PATTERN))
        .optional()
        .describe("Experience range in years, format: min-max (e.g. 3-10)"),
      lang: z
        .string()
        .regex(new RegExp(SEARCH_LANGUAGE_LIST_PATTERN))
        .optional()
        .describe("Job document language codes, comma-separated (en, de, fr, it)"),
      locale: apiLocaleSchema,
    },
    { title: "Search Jobs", readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    async (params) => {
      const data = await client.get("/api/v1/search", {
        q: params.q,
        loc: params.loc,
        occ: params.occ,
        sen: params.sen,
        tech: params.tech,
        wm: params.wm,
        etype: params.etype,
        sal: params.sal,
        exp: params.exp,
        lang: params.lang,
        locale: params.locale,
      });
      return {
        content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
      };
    },
  );
}
