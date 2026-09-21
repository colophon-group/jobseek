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

  // One scroll demand may fill at most two 50-candidate segments. This is
  // enough to get several UI pages ahead while preventing an interaction from
  // cascading through the entire historical feed.
  let result = await runCatchupStep(input);
  if (result.status === "continue") result = await runCatchupStep(input);
  return result;
}
