import "server-only";
import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { redis } from "@/lib/redis";
import { getJobAlertsConfig } from "@/lib/notifications/config";
import { cleanupNotificationRetention } from "./notification-retention";
import { runNotificationScheduler } from "./notification-scheduler";
import { deliverNotificationPlan, readNotificationQuota } from "./notification-delivery";

const LOCK = "notifications:runner:lock:v1";
const STATE = "notifications:runner:state:v1";
const RELEASE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end";
type RunState = { mode: string; day: string; sweepEnd: string; cursor: string | null; complete: boolean };

/** One daily sweep, resumable at owner-page boundaries within a 220s budget. */
export async function runJobAlerts() {
  const config = getJobAlertsConfig();
  if (config.mode === "off") return { mode: "off", status: "off" };
  const owner = randomUUID();
  if (!await redis.set(LOCK, owner, { nx: true, ex: 300 })) return { mode: config.mode, status: "overlap" };
  const started = Date.now();
  const totals = { due: 0, matched: 0, empty: 0, sent: 0, failed: 0, unknown: 0, deferred: 0, cancelled: 0, duplicate: 0 };
  try {
    const day = new Date().toISOString().slice(0, 10);
    const saved = await redis.get<RunState>(STATE);
    const state: RunState = saved?.mode === config.mode && (saved.day === day || !saved.complete) ? saved : {
      mode: config.mode, day, sweepEnd: new Date().toISOString(), cursor: null, complete: false,
    };
    if (state.complete) return { mode: config.mode, status: "complete", ...totals };
    // Persist before planning: a crash restarts this page with the same sweep.
    await redis.set(STATE, state, { ex: 172800 });
    await cleanupNotificationRetention();
    let quota = await readNotificationQuota(config);
    while (Date.now() - started < 210_000) {
      if (await redis.get(LOCK) !== owner) throw new Error("notification_lock_lost");
      if (config.mode !== "shadow") quota = await readNotificationQuota(config);
      const windowEnd = new Date(state.sweepEnd);
      const result = await runNotificationScheduler({
        mode: "shadow", sweep: { windowStart: new Date(windowEnd.getTime() - 7 * 86400000), windowEnd },
        // Live quota reservation is durable and atomic at the delivery boundary.
        quota,
        concurrency: 2, pageSize: 10, cursor: state.cursor,
        internalUserIds: config.mode === "internal" ? config.internalUserIds : undefined,
      });
      if (config.mode === "shadow") quota = { ...quota,
        dailyUsed: quota.dailyCap - result.telemetry.quotaDailyRemaining,
        monthlyUsed: quota.monthlyCap - result.telemetry.quotaMonthlyRemaining,
      };
      totals.due += result.telemetry.due;
      totals.matched += result.telemetry.matched;
      totals.empty += result.telemetry.empty;
      totals.failed += result.telemetry.failed;
      totals.duplicate += result.telemetry.duplicate;
      totals.unknown += result.telemetry.unknown;
      totals.deferred += result.telemetry.deferred;
      if (config.mode !== "shadow") {
        for (const plan of result.plans) {
          if (Date.now() - started >= 220_000) return { mode: config.mode, status: "continuation", ...totals };
          if (await redis.get(LOCK) !== owner) throw new Error("notification_lock_lost");
          try { totals[await deliverNotificationPlan(plan, config)] += 1; }
          catch { totals.unknown += 1; } // Durable barrier handles uncertain commit/send.
          await delay(750); // Bounded provider rate; reserve headroom for auth mail.
        }
      }
      state.cursor = result.continuation?.afterUserId ?? null;
      state.complete = !result.continuation;
      await redis.set(STATE, state, { ex: 172800 });
      if (state.complete) break;
    }
    if (config.mode !== "shadow") quota = await readNotificationQuota(config);
    return { mode: config.mode, status: state.complete ? "complete" : "continuation", ...totals,
      quotaDailyRemaining: Math.max(0, quota.dailyCap - quota.dailyUsed),
      quotaMonthlyRemaining: Math.max(0, quota.monthlyCap - quota.monthlyUsed) };
  } finally {
    await redis.eval(RELEASE, [LOCK], [owner]);
    console.info("job_alerts_run", { mode: config.mode, ...totals, durationMs: Date.now() - started });
  }
}
