"use client";

import type { Paddle } from "@paddle/paddle-js";

let initialization: Promise<Paddle> | undefined;

export function loadPaddle(locale: string): Promise<Paddle> {
  if (!initialization) {
    initialization = (async () => {
      const token = process.env.NEXT_PUBLIC_PADDLE_CLIENT_TOKEN;
      const environment = process.env.NEXT_PUBLIC_PADDLE_ENVIRONMENT;
      if (!token || (environment !== "sandbox" && environment !== "production") ||
          !token.startsWith(environment === "sandbox" ? "test_" : "live_")) {
        throw new Error("Paddle checkout is not configured");
      }
      const { initializePaddle } = await import("@paddle/paddle-js");
      const paddle = await initializePaddle({
        token, environment,
        checkout: { settings: {
          locale, displayMode: "overlay", allowLogout: false,
          successUrl: `${window.location.origin}/${locale}/settings/billing?checkout=complete`,
        } },
      });
      if (!paddle) throw new Error("Paddle checkout failed to initialize");
      return paddle;
    })().catch((error) => {
      initialization = undefined;
      throw error;
    });
  }
  return initialization;
}
