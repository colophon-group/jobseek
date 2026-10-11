import availability from "@/config/service-availability.json";

/** Deployment-scoped incident controls shared by server and browser. */
export function narrowedUnderMaintenance(): boolean {
  return availability.narrowedMaintenance;
}

export function subscriptionPurchasesPaused(): boolean {
  return availability.subscriptionPurchasesPaused || narrowedUnderMaintenance();
}

export class NarrowedMaintenanceError extends Error {
  constructor() {
    super("Narrowed is under maintenance");
    this.name = "NarrowedMaintenanceError";
  }
}

export function assertNarrowedAvailable(): void {
  if (narrowedUnderMaintenance()) throw new NarrowedMaintenanceError();
}
