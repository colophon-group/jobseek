import type { AiFilterCatchupStepResult } from "@/lib/ai-filter/catchup-service";

export async function runCatchupStep(input: {
  ownerId: string;
  watchlistId: string;
  leaseOwner: string;
}): Promise<AiFilterCatchupStepResult> {
  "use step";

  const { runAiFilterCatchupStep } = await import(
    "@/lib/ai-filter/catchup-service"
  );
  return runAiFilterCatchupStep(input);
}
