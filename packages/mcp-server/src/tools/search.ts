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
    "Find matching jobs sourced directly from company career pages: keywords, location, occupation, seniority, technology, remote/hybrid/onsite, employment type, EUR salary, experience and document language. Returns up to 5 companies with up to 3 posting summaries each, totalCompanies and a moreAt browsing link; no pagination or company filter. q accepts freetext; loc/occ/sen/tech use exact slugs returned by resolve_slugs or list_taxonomies. Use get_job_detail for salary and other available metadata, and create_watchlist_link for email-alert setup.",
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
        .describe("Salary bounds in EUR, integer min-max (80000-150000, 100000-, or -80000). Not the user's display currency."),
      exp: z
        .string()
        .regex(new RegExp(SEARCH_INTEGER_RANGE_PATTERN))
        .optional()
        .describe("Experience bounds in years, integer min-max (3-10, 3-, or -5)"),
      lang: z
        .string()
        .regex(new RegExp(SEARCH_LANGUAGE_LIST_PATTERN))
        .optional()
        .describe("Two-letter posting document language codes, comma-separated (e.g. en,de). Independent of response locale; omitted means all document languages."),
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
