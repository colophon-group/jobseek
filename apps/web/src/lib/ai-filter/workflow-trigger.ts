import "server-only";
import { assertNarrowedAvailable } from "@/lib/service-availability";
import { enqueueAiFilterWork } from "./work-queue";

/** Compatibility entrypoint: commits coalesced Postgres demand; never starts Workflow. */
export async function startAiFilterCatchup(input: Parameters<typeof enqueueAiFilterWork>[0]) {
  assertNarrowedAvailable();
  return enqueueAiFilterWork(input);
}
