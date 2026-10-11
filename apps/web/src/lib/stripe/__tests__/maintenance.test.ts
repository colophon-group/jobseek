import type Stripe from "stripe";
import { describe, expect, it } from "vitest";
import { billingMaintenanceUpdate } from "../maintenance";

const subscription = (overrides = {}) => ({ pause_collection: null, metadata: {}, ...overrides }) as Stripe.Subscription;

describe("operator billing collection pause", () => {
  it("marks an indefinite pause that voids future invoices", () => {
    expect(billingMaintenanceUpdate(subscription(), "pause")).toEqual({
      pause_collection: { behavior: "void" }, metadata: { jobseek_billing_maintenance: "narrowed-maintenance-v1" },
    });
  });
  it("preserves existing collection pauses and only resumes a pause owned by this control", () => {
    expect(billingMaintenanceUpdate(subscription({ pause_collection: { behavior: "keep_as_draft" } }), "pause")).toBeNull();
    expect(billingMaintenanceUpdate(subscription({ pause_collection: { behavior: "void" } }), "resume")).toBeNull();
    const owned = subscription({ metadata: { jobseek_billing_maintenance: "narrowed-maintenance-v1" }, pause_collection: { behavior: "void", resumes_at: null } });
    expect(billingMaintenanceUpdate(owned, "resume")).toEqual({ pause_collection: "", metadata: { jobseek_billing_maintenance: "" } });
    expect(billingMaintenanceUpdate(subscription({ ...owned, pause_collection: { behavior: "void", resumes_at: 2_000_000_000 } }), "resume")).toBeNull();
  });
});
