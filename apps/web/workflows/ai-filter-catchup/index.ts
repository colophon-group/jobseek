import { runCatchupStep } from "./steps";

export type AiFilterCatchupWorkflowInput = Readonly<{
  ownerId: string;
  watchlistId: string;
  leaseOwner: string;
  demandTargetOffset: number;
}>;

export async function aiFilterCatchupWorkflow(
  input: AiFilterCatchupWorkflowInput,
) {
  "use workflow";

  // One demand maintains a 500-candidate runway. Each step is still a bounded,
  // durable 50-decision segment, and the fixed cap prevents a single drawer
  // interaction from cascading through the full 10k historical ceiling.
  let result = await runCatchupStep(input);
  for (let segment = 1; segment < 10 && result.status === "continue"; segment += 1) {
    result = await runCatchupStep(input);
  }
  return result;
}
