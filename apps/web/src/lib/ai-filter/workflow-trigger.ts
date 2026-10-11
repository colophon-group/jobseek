import "server-only";
import { assertNarrowedAvailable } from "@/lib/service-availability";

import { randomUUID } from "node:crypto";
import { start } from "workflow/api";

import { aiFilterCatchupWorkflow } from "../../../workflows/ai-filter-catchup";

export async function startAiFilterCatchup(input: {
  ownerId: string;
  watchlistId: string;
  demandTargetOffset: number;
  kind?: "historical" | "freshness";
  queryVersionId?: string;
}): Promise<{ runId: string }> {
  assertNarrowedAvailable();
  const run = await start(aiFilterCatchupWorkflow, [{
    ...input,
    leaseOwner: randomUUID(),
  }]);
  return { runId: run.runId };
}
