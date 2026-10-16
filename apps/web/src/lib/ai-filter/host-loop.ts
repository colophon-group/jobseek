/** Lost notifications are recovered by the idle timeout; Postgres remains truth. */
export class WorkerWakeup {
  private pending = false;
  private resume: (() => void) | undefined;

  wake(): void {
    this.pending = true;
    this.resume?.();
  }

  async wait(milliseconds: number, signal: AbortSignal): Promise<void> {
    if (this.pending || signal.aborted) {
      this.pending = false;
      return;
    }
    await new Promise<void>((resolve) => {
      const finish = () => {
        clearTimeout(timer);
        signal.removeEventListener("abort", finish);
        resolve();
      };
      const timer = setTimeout(finish, milliseconds);
      this.resume = finish;
      signal.addEventListener("abort", finish, { once: true });
    });
    this.resume = undefined;
    this.pending = false;
  }
}

export type WorkerSlice = {
  status: "off" | "completed";
  claimed: number;
  started: number;
  deferred: number;
  failed: number;
};

export async function runHostWorker(input: {
  signal: AbortSignal;
  wakeup: WorkerWakeup;
  sweep: () => Promise<WorkerSlice>;
  report: (result: WorkerSlice | { status: "failed" }) => void;
}) {
  let failures = 0;
  let previous: string | undefined;
  while (!input.signal.aborted) {
    let delay: number;
    let failed = false;
    try {
      const result = await input.sweep();
      if (result.failed) throw new Error("worker_slice_failed");
      failures = 0;
      const busy = result.claimed > 0 || result.started > 0;
      const state = result.status === "off" ? "off" : busy ? "active" : "idle";
      if (busy || previous !== state) input.report(result);
      previous = state;
      delay = busy ? 2_000 : 30_000;
    } catch {
      failed = true;
      failures += 1;
      delay = Math.min(900_000, 30_000 * 2 ** Math.min(failures, 5));
      input.report({ status: "failed" });
      previous = "failed";
    }
    await (failed ? new WorkerWakeup() : input.wakeup).wait(delay, input.signal);
  }
}
