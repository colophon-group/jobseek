import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const webRoot = process.cwd();
const migration = readFileSync(
  resolve(webRoot, "drizzle/0088_notification_policy_foundation.sql"),
  "utf8",
);

describe("0088 notification policy foundation migration", () => {
  it("adds one extensible weekly cadence with conservative preference defaults", () => {
    expect(migration).toContain(
      "CREATE TYPE public.notification_cadence AS ENUM ('weekly')",
    );
    expect(migration).toMatch(
      /notification_cadence public\.notification_cadence\s+DEFAULT 'weekly' NOT NULL/,
    );
    expect(migration).toContain(
      "notifications_paused boolean DEFAULT false NOT NULL",
    );
    expect(migration).toMatch(
      /notifications_state_changed_at timestamp with time zone\s+DEFAULT now\(\) NOT NULL/,
    );
    expect(migration).toContain(
      "notifications_pause_state_changed_at_before_write",
    );
    expect(migration).toContain(
      "NEW.notifications_paused IS NOT DISTINCT FROM OLD.notifications_paused",
    );
    expect(migration).toContain(
      "NEW.notifications_state_changed_at := OLD.notifications_state_changed_at",
    );
  });

  it("keeps alerts opt-in and floors every existing enabled interval", () => {
    expect(migration).not.toMatch(/SET\s+alerts_enabled\s*=\s*true/i);
    expect(migration).toContain(
      "SET alerts_enabled_at = statement_timestamp()",
    );
    expect(migration).toContain("WHERE alerts_enabled = true");
    expect(migration).toContain("watchlist_alerts_enabled_at_check");
    expect(migration).toContain(
      "BEFORE INSERT OR UPDATE OF alerts_enabled, alerts_enabled_at",
    );
    expect(migration).toContain(
      "jobseek_watchlist_alerts_enabled_at_compat",
    );
  });

  it("creates the durable ledger identity and privacy-safe user lifecycle", () => {
    expect(migration).toContain("CREATE TABLE public.notification_delivery");
    expect(migration).toContain(
      "FOREIGN KEY (user_id) REFERENCES public.\"user\"(id) ON DELETE CASCADE",
    );
    expect(migration).toContain(
      "ON public.notification_delivery (user_id, cadence, scheduled_for)",
    );
    expect(migration).toContain(
      "ON public.notification_delivery (idempotency_key)",
    );
    for (const status of [
      "pending",
      "sent",
      "skipped",
      "failed",
      "unknown",
      "quota_deferred",
    ]) {
      expect(migration).toContain(`'${status}'`);
    }
  });

  it("enforces window, completion, provider-attempt, and deferral shapes", () => {
    expect(migration).toContain(
      "window_start < window_end AND window_end <= scheduled_for",
    );
    expect(migration).toContain("notification_delivery_completion_check");
    expect(migration).toContain("notification_delivery_skipped_check");
    expect(migration).toContain(
      "notification_delivery_provider_attempt_check",
    );
    expect(migration).toContain("notification_delivery_sent_provider_check");
    expect(migration).toContain("notification_delivery_deferred_check");
  });

  it("appends exactly one monotonic journal entry", () => {
    const journal = JSON.parse(
      readFileSync(resolve(webRoot, "drizzle/meta/_journal.json"), "utf8"),
    ) as { entries: { idx: number; when: number; tag: string }[] };

    expect(journal.entries.at(-1)).toEqual({
      idx: 76,
      version: "7",
      when: 1_788_199_156_000,
      tag: "0088_notification_policy_foundation",
      breakpoints: true,
    });
    expect(journal.entries.at(-2)?.when).toBeLessThan(
      journal.entries.at(-1)?.when ?? 0,
    );
  });
});
