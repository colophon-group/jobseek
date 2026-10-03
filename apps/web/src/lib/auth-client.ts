"use client";

import { createAuthClient } from "better-auth/react";
import { usernameClient } from "better-auth/client/plugins";

const ACCOUNT_IDENTITY_CHANGED = "jobseek:account-identity-changed";
const ACCOUNT_IDENTITY_CHANNEL = "jobseek-account-identity";

/** Invalidation only: no identity, cookies, session data, or network requests. */
export function notifyAccountIdentityChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(ACCOUNT_IDENTITY_CHANGED));
  if (typeof BroadcastChannel === "undefined") return;
  let channel: BroadcastChannel | undefined;
  try {
    channel = new BroadcastChannel(ACCOUNT_IDENTITY_CHANNEL);
    channel.postMessage("changed");
  } catch { /* Local invalidation still works when cross-tab messaging is blocked. */ }
  finally { channel?.close(); }
}

export function listenForAccountIdentityChanges(invalidate: () => void): () => void {
  window.addEventListener(ACCOUNT_IDENTITY_CHANGED, invalidate);
  let channel: BroadcastChannel | undefined;
  try {
    if (typeof BroadcastChannel !== "undefined") {
      channel = new BroadcastChannel(ACCOUNT_IDENTITY_CHANNEL);
      channel.onmessage = (event: MessageEvent<unknown>) => {
        if (event.data === "changed") invalidate();
      };
    }
  } catch { /* Browser lifecycle/hint checks remain available. */ }
  return () => {
    window.removeEventListener(ACCOUNT_IDENTITY_CHANGED, invalidate);
    channel?.close();
  };
}

export const authClient = createAuthClient({
  plugins: [usernameClient()],
  fetchOptions: {
    onSuccess(context) {
      if (typeof window === "undefined") return;
      const path = new URL(context.request.url, window.location.origin).pathname;
      if (
        path.startsWith("/api/auth/sign-in/") ||
        path.startsWith("/api/auth/sign-up/") ||
        ["sign-out", "delete-user", "revoke-session", "revoke-sessions", "revoke-other-sessions", "update-user", "update-session", "change-email", "change-password", "verify-email"]
          .some((operation) => path === `/api/auth/${operation}`)
      ) notifyAccountIdentityChanged();
    },
  },
});
