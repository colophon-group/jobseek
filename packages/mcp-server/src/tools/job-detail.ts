import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";
import { apiLocaleSchema } from "../locale-schema.js";

export function register(server: McpServer, client: JobseekClient) {
  server.tool(
    "get_job_detail",
    "Inspect a job returned by search_jobs using its posting ID. Returns title, company, locations, seniority, technologies, salary with original currency/period, experience, employment type, firstSeenAt and the posting URL. Fields may be missing or null; do not infer missing salary or experience. The full description and application are accessed through the returned URL, not this tool.",
    {
      id: z.string().describe("Job posting UUID (from search_jobs topPostings[].id)"),
      locale: apiLocaleSchema,
    },
    { title: "Get Job Detail", readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    async (params) => {
      const data = await client.get("/api/v1/job", {
        id: params.id,
        locale: params.locale,
      });
      return {
        content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
      };
    },
  );
}
