import type { AiFilterCatchupStepResult } from "@/lib/ai-filter/catchup-service";

export async function runCatchupStep(input: {
  ownerId: string;
  watchlistId: string;
  leaseOwner: string;
  demandTargetOffset: number;
  kind?: "historical" | "freshness";
  queryVersionId?: string;
}): Promise<AiFilterCatchupStepResult> {
  "use step";

  // Retained only to drain/reject queued legacy runs on this deployment.
  // New demand enters the shared Postgres worker. Old deployments are fenced
  // independently by migration 0104 at the segment claim boundary.
  void input;
  return { status: "disabled", segmentId: null };
}
