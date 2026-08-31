import "server-only";

import { createHash } from "node:crypto";

import type { NotificationCadence } from "./contracts";
import {
  NOTIFICATION_CADENCES,
  getNotificationMinimumIntervalMs,
} from "./policy";

const DAY_MS = 24 * 60 * 60 * 1_000;
const SCHEDULE_EPOCH_MS = Date.UTC(1970, 0, 5); // Monday, 00:00 UTC.

export const DEFAULT_NOTIFICATION_EXECUTION_MODE = "off" as const;
export const NOTIFICATION_DISPLAY_ITEM_LIMIT = 20;
export const NOTIFICATION_MATCH_LIMIT_PER_WATCHLIST = 250;
export const NOTIFICATION_CLAIM_LEASE_MS = 15 * 60 * 1_000;
export const NOTIFICATION_ELIGIBLE_OWNER_PAGE_SIZE = 50;
export const NOTIFICATION_COMPANY_MEMBERSHIP_SEGMENT_SIZE = 100;
export const MAX_NOTIFICATION_HYDRATION_SEGMENTS_PER_PERIOD = 500;
export const NOTIFICATION_MATCH_AGGREGATE_ITEM_LIMIT = 2_500;
export const MAX_NOTIFICATION_RECOVERY_SLOTS_PER_USER = 8;

export type NotificationExecutionMode = "off" | "shadow";

export type UtcWindow = Readonly<{
  windowStart: Date;
  windowEnd: Date;
}>;

export type NotificationScheduleAssignment = Readonly<{
  deliveryBucket: number;
  timeBucket: number;
  weekdayUtc: number;
  minuteOfDayUtc: number;
}>;

export type NotificationQuotaState = Readonly<{
  dailyCap: number;
  monthlyCap: number;
  dailyUsed: number;
  monthlyUsed: number;
}>;

export type NotificationQuotaDecision = Readonly<{
  allowed: boolean;
  dailyRemaining: number;
  monthlyRemaining: number;
  nextState: NotificationQuotaState;
  deferredUntil: Date | null;
}>;

function assertValidDate(value: Date, name: string): void {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) {
    throw new RangeError(`${name} must be a valid date`);
  }
}

export function assertUtcWindow(window: UtcWindow): void {
  assertValidDate(window.windowStart, "windowStart");
  assertValidDate(window.windowEnd, "windowEnd");
  if (window.windowStart.getTime() >= window.windowEnd.getTime()) {
    throw new RangeError("windowStart must be earlier than windowEnd");
  }
}

function stableUint32(
  userId: string,
  cadence: NotificationCadence,
  dimension: "delivery" | "time",
): number {
  if (!userId) throw new Error("Notification user id is required");
  return createHash("sha256")
    .update(["jobseek-notification-schedule-v1", userId, cadence, dimension].join("\n"))
    .digest()
    .readUInt32BE(0);
}

/**
 * Stable UTC placement within a cadence period. The cadence policy owns both
 * dimensions, so adding a cadence does not require a scheduler rewrite.
 */
export function getNotificationScheduleAssignment(input: {
  userId: string;
  cadence: NotificationCadence;
}): NotificationScheduleAssignment {
  const policy = NOTIFICATION_CADENCES[input.cadence];
  const deliveryBucket =
    stableUint32(input.userId, input.cadence, "delivery") %
    policy.deliveryBuckets;
  const timeBucket =
    stableUint32(input.userId, input.cadence, "time") %
    policy.timeBucketsPerDeliveryBucket;
  const bucketSpanMs =
    getNotificationMinimumIntervalMs(input.cadence) / policy.deliveryBuckets;
  const timeBucketSpanMs = bucketSpanMs / policy.timeBucketsPerDeliveryBucket;
  const minuteOfDayUtc = Math.floor(
    (timeBucket * timeBucketSpanMs) / (60 * 1_000),
  );

  return {
    deliveryBucket,
    timeBucket,
    // JavaScript uses Sunday=0; the schedule epoch and delivery bucket use
    // Monday=0, so expose the conventional UTC weekday explicitly.
    weekdayUtc: (deliveryBucket + 1) % 7,
    minuteOfDayUtc,
  };
}

/**
 * Return every stable cadence slot in the half-open UTC sweep. A caller may
 * deliberately pass more than one period for bounded operational recovery;
 * ordinary daily sweeps return either zero or one slot per user. Recovery is
 * rejected rather than truncated if it exceeds the explicit per-user bound.
 */
export function getNotificationScheduleSlots(input: {
  userId: string;
  cadence: NotificationCadence;
  sweep: UtcWindow;
}): Date[] {
  assertUtcWindow(input.sweep);
  const policy = NOTIFICATION_CADENCES[input.cadence];
  const intervalMs = getNotificationMinimumIntervalMs(input.cadence);
  const bucketSpanMs = intervalMs / policy.deliveryBuckets;
  const timeBucketSpanMs = bucketSpanMs / policy.timeBucketsPerDeliveryBucket;
  const assignment = getNotificationScheduleAssignment(input);
  const slotOffsetMs =
    assignment.deliveryBucket * bucketSpanMs +
    assignment.timeBucket * timeBucketSpanMs;
  const firstPeriod = Math.floor(
    (input.sweep.windowStart.getTime() - SCHEDULE_EPOCH_MS - slotOffsetMs) /
      intervalMs,
  );
  const slots: Date[] = [];

  // Include the preceding period because a slot can sit exactly on the
  // sweep's inclusive start. The half-open comparisons remove extra values.
  for (
    let period = firstPeriod;
    ;
    period += 1
  ) {
    const slotMs = SCHEDULE_EPOCH_MS + period * intervalMs + slotOffsetMs;
    if (slotMs >= input.sweep.windowEnd.getTime()) break;
    if (slotMs >= input.sweep.windowStart.getTime()) {
      if (slots.length >= MAX_NOTIFICATION_RECOVERY_SLOTS_PER_USER) {
        throw new RangeError(
          `sweep exceeds ${MAX_NOTIFICATION_RECOVERY_SLOTS_PER_USER} recovery slots`,
        );
      }
      slots.push(new Date(slotMs));
    }
  }
  return slots;
}

function assertQuotaInteger(value: number, name: string): void {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new RangeError(`${name} must be a non-negative safe integer`);
  }
}

function nextUtcDay(now: Date): Date {
  return new Date(Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate() + 1,
  ));
}

function nextUtcMonth(now: Date): Date {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1));
}

/**
 * Pure quota calculation. Caps and usage are caller-supplied snapshots; this
 * module defines no live configuration and performs no external reservation.
 */
export function calculateNotificationQuota(input: {
  state: NotificationQuotaState;
  requested: number;
  now: Date;
}): NotificationQuotaDecision {
  assertValidDate(input.now, "now");
  assertQuotaInteger(input.state.dailyCap, "dailyCap");
  assertQuotaInteger(input.state.monthlyCap, "monthlyCap");
  assertQuotaInteger(input.state.dailyUsed, "dailyUsed");
  assertQuotaInteger(input.state.monthlyUsed, "monthlyUsed");
  assertQuotaInteger(input.requested, "requested");

  const dailyRemainingBefore = Math.max(
    0,
    input.state.dailyCap - input.state.dailyUsed,
  );
  const monthlyRemainingBefore = Math.max(
    0,
    input.state.monthlyCap - input.state.monthlyUsed,
  );
  const dailyBlocked = input.requested > dailyRemainingBefore;
  const monthlyBlocked = input.requested > monthlyRemainingBefore;
  const allowed = !dailyBlocked && !monthlyBlocked;
  const nextState = allowed
    ? {
        ...input.state,
        dailyUsed: input.state.dailyUsed + input.requested,
        monthlyUsed: input.state.monthlyUsed + input.requested,
      }
    : input.state;
  const resetCandidates = [
    dailyBlocked ? nextUtcDay(input.now) : null,
    monthlyBlocked ? nextUtcMonth(input.now) : null,
  ].filter((value): value is Date => value !== null);

  return {
    allowed,
    dailyRemaining: Math.max(0, nextState.dailyCap - nextState.dailyUsed),
    monthlyRemaining: Math.max(
      0,
      nextState.monthlyCap - nextState.monthlyUsed,
    ),
    nextState,
    deferredUntil:
      resetCandidates.length === 0
        ? null
        : new Date(Math.max(...resetCandidates.map((value) => value.getTime()))),
  };
}

/** Run async work with a hard in-process concurrency bound, preserving order. */
export async function mapWithConcurrency<T, R>(
  values: readonly T[],
  concurrency: number,
  operation: (value: T, index: number) => Promise<R>,
): Promise<R[]> {
  if (!Number.isSafeInteger(concurrency) || concurrency < 1) {
    throw new RangeError("concurrency must be a positive safe integer");
  }
  const results = new Array<R>(values.length);
  let nextIndex = 0;
  const workers = Array.from(
    { length: Math.min(concurrency, values.length) },
    async () => {
      while (nextIndex < values.length) {
        const index = nextIndex;
        nextIndex += 1;
        results[index] = await operation(values[index]!, index);
      }
    },
  );
  await Promise.all(workers);
  return results;
}

export const NOTIFICATION_SCHEDULE_DAY_MS = DAY_MS;
