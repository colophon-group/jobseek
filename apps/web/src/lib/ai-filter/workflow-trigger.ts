import "server-only";

import { randomUUID } from "node:crypto";
import { start } from "workflow/api";

import { aiFilterCatchupWorkflow } from "../../../workflows/ai-filter-catchup";

export async function startAiFilterCatchup(input: {
  ownerId: string;
  watchlistId: string;
  demandTargetOffset: number;
}): Promise<{ runId: string }> {
  const run = await start(aiFilterCatchupWorkflow, [{
    ...input,
    leaseOwner: randomUUID(),
  }]);
  return { runId: run.runId };
}
