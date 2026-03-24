import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";

export function register(server: McpServer, client: JobseekClient) {
  server.tool(
    "get_job_detail",
    "Get full structured metadata for a job posting by ID. Returns salary, technologies, seniority, experience, and locations. Does not include the job description — visit the returned URL on jseek.co to read the full posting. Use posting IDs from search_jobs results.",
    {
      id: z.string().describe("Job posting UUID (from search_jobs topPostings[].id)"),
      locale: z
        .enum(["en", "de", "fr", "it"])
        .default("en")
        .describe("Response language"),
    },
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
