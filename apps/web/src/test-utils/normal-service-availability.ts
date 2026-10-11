import { vi } from "vitest";

// Existing product suites exercise normal availability, independently of the
// current production incident. Maintenance suites override this fixture.
vi.mock("@/config/service-availability.json", () => ({
  default: { narrowedWorkerPaused:false, narrowedMaintenance: false, subscriptionPurchasesPaused: false, subscriptionBillingPaused: false },
}));
