import { runCatchupStep } from "./steps";

export type AiFilterCatchupWorkflowInput = Readonly<{
  ownerId: string;
  watchlistId: string;
  leaseOwner: string;
}>;

export async function aiFilterCatchupWorkflow(
  input: AiFilterCatchupWorkflowInput,
) {
  "use workflow";

  while (true) {
    const result = await runCatchupStep(input);
    if (result.status !== "continue") return result;
  }
}
