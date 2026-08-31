import "server-only";

import { createHash } from "node:crypto";

import {
  NOTIFICATION_CADENCE_VALUES,
  type NotificationCadence,
  type NotificationDeliveryStatus,
} from "./contracts";

const DAY_MS = 24 * 60 * 60 * 1_000;

export type NotificationCadencePolicy = Readonly<{
  minimumIntervalDays: number;
  deliveryBuckets: number;
}>;

/**
 * Cadence is policy, not matcher behavior. Callers derive explicit window
 * bounds from this map and pass those bounds to the canonical matcher.
 */
export const NOTIFICATION_CADENCES = {
  weekly: {
    minimumIntervalDays: 7,
    deliveryBuckets: 7,
  },
} as const satisfies Record<NotificationCadence, NotificationCadencePolicy>;

export const DEFAULT_NOTIFICATION_CADENCE: NotificationCadence = "weekly";

/**
 * A bounded automatic retry budget for definitive failures. An `unknown`
 * outcome is deliberately not retryable because the provider may have
 * accepted the message even when its response was lost.
 */
export const MAX_NOTIFICATION_PROVIDER_ATTEMPTS = 3;

/**
 * Completed identities stay long enough to cover more than a full year of
 * weekly periods. Non-terminal rows are never automatically retention-eligible
 * so unresolved failures cannot silently lose their idempotency barrier.
 */
export const NOTIFICATION_DELIVERY_RETENTION_DAYS = 400;

const DELIVERY_TRANSITIONS = {
  pending: ["skipped", "failed", "unknown", "quota_deferred"],
  unknown: ["sent", "failed"],
  failed: ["pending"],
  quota_deferred: ["pending"],
  sent: [],
  skipped: [],
} as const satisfies Record<
  NotificationDeliveryStatus,
  readonly NotificationDeliveryStatus[]
>;

export type NotificationPauseState = Readonly<{
  notificationsPaused: boolean;
  notificationsStateChangedAt: Date;
}>;

export type WatchlistAlertState = Readonly<{
  alertsEnabled: boolean;
  alertsEnabledAt: Date | null;
}>;

export type WatchlistAlertTransition =
  | Readonly<{
      ok: true;
      state: WatchlistAlertState;
    }>
  | Readonly<{
      ok: false;
      error: "notifications_paused";
      state: WatchlistAlertState;
    }>;

export type NotificationWindowFloorInput = Readonly<{
  alertsEnabledAt: Date;
  notificationsStateChangedAt: Date;
  lastProcessedWindowEnd?: Date | null;
}>;

export type NotificationProviderAttemptState = Readonly<{
  status: NotificationDeliveryStatus;
  providerAttemptCount: number;
  lastProviderAttemptAt: Date | null;
}>;

export function resolveNotificationCadence(
  value: unknown,
): NotificationCadence {
  return typeof value === "string" &&
    (NOTIFICATION_CADENCE_VALUES as readonly string[]).includes(value)
    ? (value as NotificationCadence)
    : DEFAULT_NOTIFICATION_CADENCE;
}

export function getNotificationMinimumIntervalMs(
  cadence: NotificationCadence,
): number {
  return NOTIFICATION_CADENCES[cadence].minimumIntervalDays * DAY_MS;
}

/** Exact server-side eligibility precedence from #8317. */
export function isWatchlistNotificationEligible(input: {
  alertsEnabled: boolean;
  alertsEnabledAt: Date | null;
  notificationsPaused: boolean;
}): boolean {
  return (
    input.alertsEnabled &&
    input.alertsEnabledAt !== null &&
    !input.notificationsPaused
  );
}

/**
 * A repeated pause/resume request is idempotent and does not move the floor.
 * A real state change records its own timestamp without touching watchlists.
 */
export function transitionNotificationPause(
  current: NotificationPauseState,
  notificationsPaused: boolean,
  changedAt: Date,
): NotificationPauseState {
  if (current.notificationsPaused === notificationsPaused) return current;
  return { notificationsPaused, notificationsStateChangedAt: changedAt };
}

/**
 * Per-watchlist toggles are inert while globally paused. Enabling or
 * re-enabling records a new floor; disabling clears the active interval.
 */
export function toggleWatchlistAlertState(
  current: WatchlistAlertState,
  notificationsPaused: boolean,
  changedAt: Date,
): WatchlistAlertTransition {
  if (notificationsPaused) {
    return { ok: false, error: "notifications_paused", state: current };
  }

  const alertsEnabled = !current.alertsEnabled;
  return {
    ok: true,
    state: {
      alertsEnabled,
      alertsEnabledAt: alertsEnabled ? changedAt : null,
    },
  };
}

/**
 * The state-change timestamp is deliberately included even while unpaused:
 * migration establishes a no-backlog floor, and every resume replaces it so
 * jobs accumulated during a pause can never enter a later window.
 */
export function getNotificationWindowFloor(
  input: NotificationWindowFloorInput,
): Date {
  const candidates = [
    input.alertsEnabledAt,
    input.notificationsStateChangedAt,
    input.lastProcessedWindowEnd,
  ].filter((value): value is Date => value instanceof Date);

  return new Date(Math.max(...candidates.map((value) => value.getTime())));
}

export function canTransitionNotificationDelivery(
  from: NotificationDeliveryStatus,
  to: NotificationDeliveryStatus,
  providerAttemptCount = 0,
): boolean {
  if (from === "failed" && to === "pending") {
    return providerAttemptCount < MAX_NOTIFICATION_PROVIDER_ATTEMPTS;
  }
  return (DELIVERY_TRANSITIONS[from] as readonly NotificationDeliveryStatus[])
    .includes(to);
}

/**
 * Move to `unknown` before the external side effect. If the process dies
 * during the provider call, the durable row therefore fails closed instead
 * of being retried as though no send had happened.
 */
export function beginNotificationProviderAttempt(
  current: NotificationProviderAttemptState,
  attemptedAt: Date,
): NotificationProviderAttemptState {
  if (current.status !== "pending") {
    throw new Error("A provider attempt can start only from pending");
  }
  if (
    current.providerAttemptCount >= MAX_NOTIFICATION_PROVIDER_ATTEMPTS
  ) {
    throw new Error("Notification provider attempt budget exhausted");
  }
  return {
    status: "unknown",
    providerAttemptCount: current.providerAttemptCount + 1,
    lastProviderAttemptAt: attemptedAt,
  };
}

export function shouldAutomaticallyRetryNotificationDelivery(input: {
  status: NotificationDeliveryStatus;
  providerAttemptCount: number;
  deferredUntil?: Date | null;
  now: Date;
}): boolean {
  if (input.status === "failed") {
    return input.providerAttemptCount < MAX_NOTIFICATION_PROVIDER_ATTEMPTS;
  }
  if (input.status === "quota_deferred") {
    return (
      input.deferredUntil !== null &&
      input.deferredUntil !== undefined &&
      input.deferredUntil.getTime() <= input.now.getTime()
    );
  }
  return false;
}

export function advancesNotificationWindow(
  status: NotificationDeliveryStatus,
): boolean {
  return status === "sent" || status === "skipped";
}

export function isNotificationDeliveryRetentionEligible(input: {
  status: NotificationDeliveryStatus;
  completedAt: Date | null;
  now: Date;
}): boolean {
  if (!advancesNotificationWindow(input.status) || !input.completedAt) {
    return false;
  }
  const ageMs = input.now.getTime() - input.completedAt.getTime();
  return ageMs >= NOTIFICATION_DELIVERY_RETENTION_DAYS * DAY_MS;
}

/**
 * Provider-safe, privacy-minimizing identity for one user/cadence/period.
 * The database stores this key and separately enforces the same source tuple.
 */
export function createNotificationDeliveryIdempotencyKey(input: {
  userId: string;
  cadence: NotificationCadence;
  scheduledFor: Date;
}): string {
  if (!input.userId) throw new Error("Notification user id is required");
  if (Number.isNaN(input.scheduledFor.getTime())) {
    throw new Error("Notification scheduled time is invalid");
  }
  const canonical = [
    "jobseek-notification-v1",
    input.userId,
    input.cadence,
    input.scheduledFor.toISOString(),
  ].join("\n");
  return `jobseek-notification-v1:${createHash("sha256")
    .update(canonical)
    .digest("hex")}`;
}
