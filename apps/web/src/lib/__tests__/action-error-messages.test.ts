import { describe, expect, it } from "vitest";
import { translateActionError, type ActionErrorCode } from "@/lib/action-error-messages";

const t = ((descriptor: { message?: string; id?: string }) =>
  descriptor.message ?? descriptor.id ?? "") as Parameters<
  typeof translateActionError
>[0];

describe("translateActionError", () => {
  it.each([
    ["not_authenticated", "Please log in and try again."],
    ["not_found", "Not found."],
    ["payments_unavailable", "Payments are not available yet."],
    ["billing_account_not_found", "No billing account found."],
    ["billing_portal_unavailable", "Billing portal is not available yet."],
    ["password_set_failed", "Failed to set password."],
    ["username_length", "Username must be 3-30 characters."],
    [
      "username_invalid_characters",
      "Username can only use lowercase letters, numbers, and hyphens, and cannot start or end with a hyphen.",
    ],
    ["username_reserved", "This username is reserved."],
    ["username_update_failed", "Could not update username."],
    ["user_not_found", "User not found."],
    ["invalid_status_transition", "That status change is not allowed."],
    ["interview_round_failed", "Could not assign interview round."],
  ] satisfies Array<[ActionErrorCode, string]>)(
    "maps %s to a localized descriptor",
    (code, message) => {
      expect(translateActionError(t, code)).toBe(message);
    },
  );
});
