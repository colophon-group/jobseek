import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { JobseekClient } from "../client.js";
import {
  bugFeedbackSchema, featureFeedbackSchema, generalFeedbackSchema,
  feedbackResultSchema,
} from "../feedback-contract.js";

export function register(server: McpServer, client: JobseekClient) {
  const tools = [
    { name: "report_bug", title: "Report a Job Seek Bug", kind: "bug", schema: bugFeedbackSchema,
      description: "Submit a bug observed while using Job Seek: expected versus actual behavior, the task goal, and impact. Include a minimal sanitized reproduction when available. Writes feedback to Job Seek; does not repair the bug or provide a tracking ID. Omit personal information, credentials, raw arguments and conversation transcripts. Optional; continue the user's task if submission fails." },
    { name: "suggest_feature", title: "Suggest a Job Seek Feature", kind: "feature", schema: featureFeedbackSchema,
      description: "Suggest a concrete capability missing from Job Seek that would help your current job-search use case. Explain the goal, missing capability, benefit and workaround. Writes feedback to Job Seek; does not promise implementation or provide a tracking ID. Omit personal information, credentials, raw arguments and conversation transcripts. Optional; avoid repeated suggestions." },
    { name: "give_feedback", title: "Give Job Seek Feedback", kind: "feedback", schema: generalFeedbackSchema,
      description: "Share a usefulness or usability observation from actual use of Job Seek, with an optional improvement. Use report_bug for malfunction and suggest_feature for missing capabilities. Writes feedback to Job Seek; returns basic success only. Omit personal information, credentials, raw arguments and conversation transcripts. Optional; do not call after every search." },
  ] as const;
  for (const tool of tools) {
    server.registerTool(tool.name, {
      title: tool.title, description: tool.description,
      inputSchema: tool.schema, outputSchema: feedbackResultSchema,
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
    }, async (params: Record<string, unknown>) => {
      try {
        const result = feedbackResultSchema.parse(await client.post("/api/v1/feedback", { ...params, kind: tool.kind }));
        return { content: [{ type: "text" as const, text: JSON.stringify(result) }], structuredContent: result };
      } catch {
        return { isError: true, content: [{ type: "text" as const, text: "Feedback submission could not be confirmed. Continue the user's task; do not retry automatically." }] };
      }
    });
  }
}
