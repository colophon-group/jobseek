import "server-only";

export type JobAlertsMode = "off" | "shadow" | "internal" | "live";
export type JobAlertsConfig = Readonly<{
  mode: JobAlertsMode;
  dailyCap: number;
  monthlyCap: number;
  internalUserIds: readonly string[];
}>;

/** Invalid configuration fails closed; defaults leave room for account email. */
export function getJobAlertsConfig(env: Record<string, string | undefined> = process.env): JobAlertsConfig {
  const mode = env.JOB_ALERTS_MODE ?? "off";
  if (!["off", "shadow", "internal", "live"].includes(mode)) throw new Error("Invalid JOB_ALERTS_MODE");
  function cap(key: string, fallback: number, ceiling: number) {
    const raw = env[key] ?? String(fallback);
    if (!/^\d+$/.test(raw) || Number(raw) > ceiling) throw new Error(`Invalid ${key}`);
    return Number(raw);
  }
  const internalUserIds = (env.JOB_ALERTS_INTERNAL_USER_IDS ?? "").split(",").map(s => s.trim()).filter(Boolean);
  if (mode === "internal" && !internalUserIds.length) throw new Error("Internal recipients are required");
  if (mode === "live" || mode === "internal") {
    if (!env.RESEND_API_KEY || (env.JOB_ALERTS_UNSUBSCRIBE_SECRET?.length ?? 0) < 32 || !env.RESEND_WEBHOOK_SECRET) {
      throw new Error("Notification provider, unsubscribe and webhook secrets are required");
    }
    // Preview deployments must never send real notification mail.
    if (env.VERCEL_ENV && env.VERCEL_ENV !== "production") throw new Error("Notification delivery is production-only");
  }
  return {
    mode: mode as JobAlertsMode,
    dailyCap: cap("JOB_ALERTS_DAILY_CAP", 75, 75),
    monthlyCap: cap("JOB_ALERTS_MONTHLY_CAP", 2400, 2400),
    internalUserIds,
  };
}
