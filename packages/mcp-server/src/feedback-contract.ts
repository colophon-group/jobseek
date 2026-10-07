import { z } from "zod";
import { JOBSEEK_TOOL_NAMES } from "./metadata.js";

export const MAX_FEEDBACK_BYTES = 16_384;
const text = (max: number) => z.string().trim().min(1).max(max);
const common = {
  goal: text(500).describe("Task you were trying to accomplish, summarized without personal information or the original conversation"),
  affectedTool: z.enum(JOBSEEK_TOOL_NAMES).optional().describe("Job Seek tool involved, if applicable"),
};
const impact = z.enum(["blocked", "partial", "inconvenience"]).describe("How the problem affected completion of the task");
export const bugFeedbackSchema = z.strictObject({
  ...common,
  expected: text(1000).describe("Expected behavior"),
  observed: text(1000).describe("What actually happened; distinguish observations from assumptions"),
  reproduction: text(1000).optional().describe("Minimal sanitized reproduction; no raw arguments, credentials or transcripts"),
  impact,
  workaround: text(500).optional().describe("Workaround, if any"),
});
export const featureFeedbackSchema = z.strictObject({
  ...common,
  capability: text(1000).describe("Concrete missing capability"),
  benefit: text(1000).describe("How the capability would improve the task outcome"),
  impact,
  workaround: text(500).optional().describe("How you currently work around the limitation"),
});
export const generalFeedbackSchema = z.strictObject({
  ...common,
  observation: text(1000).describe("What was useful or confusing, based on actual use"),
  improvement: text(1000).optional().describe("Optional suggested improvement"),
});
export const feedbackSubmissionSchema = z.discriminatedUnion("kind", [
  bugFeedbackSchema.extend({ kind: z.literal("bug") }),
  featureFeedbackSchema.extend({ kind: z.literal("feature") }),
  generalFeedbackSchema.extend({ kind: z.literal("feedback") }),
]);
export const feedbackResultSchema = z.strictObject({ success: z.literal(true) });
export type FeedbackSubmission = z.infer<typeof feedbackSubmissionSchema>;
