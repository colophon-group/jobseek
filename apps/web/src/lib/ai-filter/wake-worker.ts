import "server-only";
import { after } from "next/server";
import { runAiFilterRefreshSweep } from "./refresh-service";

/** A wake-up only. Durable work, capacity, leases and retry belong to Postgres. */
export function wakeAiFilterWorker(): void {
  after(async () => {
    try {
      const result = await runAiFilterRefreshSweep();
      console.info("ai_filter_owner_wake", result);
    } catch {
      // The protected host runner recovers queued work even if this request dies.
      console.warn("ai_filter_owner_wake_failed");
    }
  });
}
