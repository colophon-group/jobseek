import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  NOTIFICATION_CADENCE_VALUES,
  NOTIFICATION_DELIVERY_STATUS_VALUES,
  type NotificationDeliveryStatus,
} from "../contracts";
import {
  DEFAULT_NOTIFICATION_CADENCE,
  MAX_NOTIFICATION_PROVIDER_ATTEMPTS,
  NOTIFICATION_CADENCES,
  NOTIFICATION_DELIVERY_RETENTION_DAYS,
  advancesNotificationWindow,
  beginNotificationProviderAttempt,
  canTransitionNotificationDelivery,
  createNotificationDeliveryIdempotencyKey,
  getNotificationMinimumIntervalMs,
  getNotificationWindowFloor,
  isNotificationDeliveryRetentionEligible,
  isWatchlistNotificationEligible,
  resolveNotificationCadence,
  shouldAutomaticallyRetryNotificationDelivery,
  toggleWatchlistAlertState,
  transitionNotificationPause,
} from "../policy";

const dayMs = 24 * 60 * 60 * 1_000;

describe("notification cadence policy", () => {
  it("keeps the persisted cadence tuple and typed policy map in sync", () => {
    expect(Object.keys(NOTIFICATION_CADENCES)).toEqual([
      ...NOTIFICATION_CADENCE_VALUES,
    ]);
    expect(DEFAULT_NOTIFICATION_CADENCE).toBe("weekly");
    expect(getNotificationMinimumIntervalMs("weekly")).toBe(7 * dayMs);
    expect(NOTIFICATION_CADENCES.weekly.deliveryBuckets).toBe(7);
  });

  it("fails closed to the weekly fallback for absent or unknown values", () => {
    expect(resolveNotificationCadence(null)).toBe("weekly");
    expect(resolveNotificationCadence("daily")).toBe("weekly");
    expect(resolveNotificationCadence("weekly")).toBe("weekly");
  });
});

describe("pause, enable, and window-floor transitions", () => {
  const initial = new Date("2026-08-01T08:00:00.000Z");
  const pausedAt = new Date("2026-08-10T08:00:00.000Z");
  const resumedAt = new Date("2026-08-20T08:00:00.000Z");

  it("uses the exact enabled-and-not-paused server eligibility precedence", () => {
    expect(
      isWatchlistNotificationEligible({
        alertsEnabled: true,
        alertsEnabledAt: initial,
        notificationsPaused: false,
      }),
    ).toBe(true);
    expect(
      isWatchlistNotificationEligible({
        alertsEnabled: true,
        alertsEnabledAt: initial,
        notificationsPaused: true,
      }),
    ).toBe(false);
    expect(
      isWatchlistNotificationEligible({
        alertsEnabled: false,
        alertsEnabledAt: null,
        notificationsPaused: false,
      }),
    ).toBe(false);
  });

  it("preserves watchlist state while paused and refreshes each enable floor", () => {
    const disabled = { alertsEnabled: false, alertsEnabledAt: null };
    const blocked = toggleWatchlistAlertState(disabled, true, pausedAt);
    expect(blocked).toEqual({
      ok: false,
      error: "notifications_paused",
      state: disabled,
    });

    const enabled = toggleWatchlistAlertState(disabled, false, initial);
    expect(enabled).toEqual({
      ok: true,
      state: { alertsEnabled: true, alertsEnabledAt: initial },
    });
    if (!enabled.ok) throw new Error("expected enabled transition");

    const disabledAgain = toggleWatchlistAlertState(
      enabled.state,
      false,
      pausedAt,
    );
    expect(disabledAgain).toEqual({
      ok: true,
      state: { alertsEnabled: false, alertsEnabledAt: null },
    });
    if (!disabledAgain.ok) throw new Error("expected disabled transition");

    expect(
      toggleWatchlistAlertState(disabledAgain.state, false, resumedAt),
    ).toEqual({
      ok: true,
      state: { alertsEnabled: true, alertsEnabledAt: resumedAt },
    });
  });

  it("moves the global floor only on a real pause or resume", () => {
    const unpaused = {
      notificationsPaused: false,
      notificationsStateChangedAt: initial,
    };
    expect(transitionNotificationPause(unpaused, false, pausedAt)).toBe(
      unpaused,
    );

    const paused = transitionNotificationPause(unpaused, true, pausedAt);
    expect(paused).toEqual({
      notificationsPaused: true,
      notificationsStateChangedAt: pausedAt,
    });
    expect(transitionNotificationPause(paused, true, resumedAt)).toBe(paused);
    expect(transitionNotificationPause(paused, false, resumedAt)).toEqual({
      notificationsPaused: false,
      notificationsStateChangedAt: resumedAt,
    });
  });

  it("floors the next window after enable, resume, and prior advancement", () => {
    expect(
      getNotificationWindowFloor({
        alertsEnabledAt: initial,
        notificationsStateChangedAt: resumedAt,
        lastProcessedWindowEnd: pausedAt,
      }),
    ).toEqual(resumedAt);

    const lastProcessed = new Date("2026-08-25T08:00:00.000Z");
    expect(
      getNotificationWindowFloor({
        alertsEnabledAt: initial,
        notificationsStateChangedAt: resumedAt,
        lastProcessedWindowEnd: lastProcessed,
      }),
    ).toEqual(lastProcessed);
  });
});

describe("durable delivery state policy", () => {
  const allowed: Record<
    NotificationDeliveryStatus,
    NotificationDeliveryStatus[]
  > = {
    pending: ["skipped", "failed", "unknown", "quota_deferred"],
    unknown: ["sent", "failed"],
    failed: ["pending"],
    quota_deferred: ["pending"],
    sent: [],
    skipped: [],
  };

  it("allows only the documented state transitions", () => {
    for (const from of NOTIFICATION_DELIVERY_STATUS_VALUES) {
      for (const to of NOTIFICATION_DELIVERY_STATUS_VALUES) {
        expect(
          canTransitionNotificationDelivery(from, to, 0),
          `${from} -> ${to}`,
        ).toBe(allowed[from].includes(to));
      }
    }
    expect(
      canTransitionNotificationDelivery(
        "failed",
        "pending",
        MAX_NOTIFICATION_PROVIDER_ATTEMPTS,
      ),
    ).toBe(false);
  });

  it("records an attempt as unknown before the provider side effect", () => {
    const attemptedAt = new Date("2026-08-31T08:00:00.000Z");
    expect(
      beginNotificationProviderAttempt(
        {
          status: "pending",
          providerAttemptCount: 1,
          lastProviderAttemptAt: null,
        },
        attemptedAt,
      ),
    ).toEqual({
      status: "unknown",
      providerAttemptCount: 2,
      lastProviderAttemptAt: attemptedAt,
    });
    expect(() =>
      beginNotificationProviderAttempt(
        {
          status: "unknown",
          providerAttemptCount: 1,
          lastProviderAttemptAt: attemptedAt,
        },
        attemptedAt,
      ),
    ).toThrow("only from pending");
    expect(() =>
      beginNotificationProviderAttempt(
        {
          status: "pending",
          providerAttemptCount: MAX_NOTIFICATION_PROVIDER_ATTEMPTS,
          lastProviderAttemptAt: attemptedAt,
        },
        attemptedAt,
      ),
    ).toThrow("budget exhausted");
  });

  it("retries definitive failures and due quota deferrals, never unknowns", () => {
    const now = new Date("2026-08-31T08:00:00.000Z");
    expect(
      shouldAutomaticallyRetryNotificationDelivery({
        status: "failed",
        providerAttemptCount: 2,
        now,
      }),
    ).toBe(true);
    expect(
      shouldAutomaticallyRetryNotificationDelivery({
        status: "failed",
        providerAttemptCount: 3,
        now,
      }),
    ).toBe(false);
    expect(
      shouldAutomaticallyRetryNotificationDelivery({
        status: "quota_deferred",
        providerAttemptCount: 0,
        deferredUntil: new Date(now.getTime() - 1),
        now,
      }),
    ).toBe(true);
    expect(
      shouldAutomaticallyRetryNotificationDelivery({
        status: "quota_deferred",
        providerAttemptCount: 0,
        deferredUntil: new Date(now.getTime() + 1),
        now,
      }),
    ).toBe(false);
    expect(
      shouldAutomaticallyRetryNotificationDelivery({
        status: "unknown",
        providerAttemptCount: 1,
        now,
      }),
    ).toBe(false);
  });

  it("advances windows only for sent and zero-match skipped outcomes", () => {
    expect(advancesNotificationWindow("sent")).toBe(true);
    expect(advancesNotificationWindow("skipped")).toBe(true);
    for (const status of [
      "pending",
      "failed",
      "unknown",
      "quota_deferred",
    ] as const) {
      expect(advancesNotificationWindow(status)).toBe(false);
    }
  });

  it("retains unresolved identities and expires only old completed rows", () => {
    const completedAt = new Date("2025-01-01T00:00:00.000Z");
    const beforeCutoff = new Date(
      completedAt.getTime() + NOTIFICATION_DELIVERY_RETENTION_DAYS * dayMs - 1,
    );
    const atCutoff = new Date(beforeCutoff.getTime() + 1);
    expect(
      isNotificationDeliveryRetentionEligible({
        status: "sent",
        completedAt,
        now: beforeCutoff,
      }),
    ).toBe(false);
    expect(
      isNotificationDeliveryRetentionEligible({
        status: "skipped",
        completedAt,
        now: atCutoff,
      }),
    ).toBe(true);
    expect(
      isNotificationDeliveryRetentionEligible({
        status: "unknown",
        completedAt,
        now: atCutoff,
      }),
    ).toBe(false);
  });

  it("derives a stable, opaque key from the unique delivery identity", () => {
    const scheduledFor = new Date("2026-09-01T08:00:00.000Z");
    const first = createNotificationDeliveryIdempotencyKey({
      userId: "user-1",
      cadence: "weekly",
      scheduledFor,
    });
    expect(first).toBe(
      createNotificationDeliveryIdempotencyKey({
        userId: "user-1",
        cadence: "weekly",
        scheduledFor,
      }),
    );
    expect(first).not.toContain("user-1");
    expect(first).not.toBe(
      createNotificationDeliveryIdempotencyKey({
        userId: "user-2",
        cadence: "weekly",
        scheduledFor,
      }),
    );
  });
});
