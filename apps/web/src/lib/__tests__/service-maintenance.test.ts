import { beforeEach, describe, expect, it, vi } from "vitest";

const controls = vi.hoisted(() => ({ narrowedMaintenance: true, subscriptionPurchasesPaused: true, subscriptionBillingPaused: true }));
const mocks = vi.hoisted(() => ({ start: vi.fn(), stripe: vi.fn() }));
vi.mock("@/config/service-availability.json", () => ({ default: controls }));
vi.mock("server-only", () => ({}));
vi.mock("workflow/api", () => ({ start: mocks.start }));
vi.mock("@/lib/stripe/client", () => ({ getStripe: mocks.stripe }));
vi.mock("@/lib/stripe/checkout-policy", () => ({ checkoutPolicyMessage: vi.fn() }));

import { assertNarrowedAvailable, narrowedUnderMaintenance, subscriptionPurchasesPaused } from "../service-availability";
import { canRunAiFilter, readAiFilterRuntimePolicy } from "../ai-filter/policy";
import { startAiFilterCatchup } from "../ai-filter/workflow-trigger";
import { aiFilterErrorResponse } from "../ai-filter/route-utils";
import { NarrowedMaintenanceError } from "../service-availability";
import { checkout } from "../stripe/checkout";

beforeEach(() => {
  Object.assign(controls, { narrowedMaintenance: true, subscriptionPurchasesPaused: true });
  vi.clearAllMocks();
});

describe("service maintenance admission", () => {
  it("blocks Workflow creation even with both execution switches enabled", async () => {
    const policy = readAiFilterRuntimePolicy({ AI_FILTER_ENABLED: "true", AI_FILTER_JEV_1_13_0_ENABLED: "true" });
    expect(canRunAiFilter(policy)).toBe(false);
    await expect(startAiFilterCatchup({ ownerId: "owner", watchlistId: "watchlist", demandTargetOffset: 500 }))
      .rejects.toThrow(NarrowedMaintenanceError);
    expect(mocks.start).not.toHaveBeenCalled();
  });

  it("allows the ordinary execution switches to govern after an explicit reopening", () => {
    controls.narrowedMaintenance = false;
    expect(canRunAiFilter(readAiFilterRuntimePolicy({ AI_FILTER_ENABLED: "true", AI_FILTER_JEV_1_13_0_ENABLED: "true" }))).toBe(true);
    expect(() => assertNarrowedAvailable()).not.toThrow();
    expect(subscriptionPurchasesPaused()).toBe(true);
  });

  it("independently blocks direct checkout before payment or database access", async () => {
    controls.narrowedMaintenance = false;
    expect(narrowedUnderMaintenance()).toBe(false);
    await expect(checkout({ id: "owner", email: "fixture@example.com" })).rejects.toMatchObject({ code: "payments_unavailable" });
    expect(mocks.stripe).not.toHaveBeenCalled();
  });

  it("returns explicit maintenance without a short automatic retry instruction", async () => {
    const response = aiFilterErrorResponse(new NarrowedMaintenanceError());
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "maintenance" });
    expect(response.headers.get("retry-after")).toBeNull();
  });
});
