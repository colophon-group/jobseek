/**
 * Persisted notification values shared by Drizzle and the server policy.
 *
 * Adding a cadence is intentionally a coordinated change: extend this tuple,
 * the policy map, and the PostgreSQL enum migration. Matching and ledger code
 * consume the generic `NotificationCadence` type and do not special-case a
 * seven-day window.
 */
export const NOTIFICATION_CADENCE_VALUES = ["weekly"] as const;

export type NotificationCadence =
  (typeof NOTIFICATION_CADENCE_VALUES)[number];

export const NOTIFICATION_DELIVERY_STATUS_VALUES = [
  "pending",
  "sent",
  "skipped",
  "failed",
  "unknown",
  "quota_deferred",
] as const;

export type NotificationDeliveryStatus =
  (typeof NOTIFICATION_DELIVERY_STATUS_VALUES)[number];
