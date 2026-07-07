import type { useLingui } from "@lingui/react/macro";
import type { BillingActionErrorCode } from "@/lib/actions/billing";
import type { MyJobsActionErrorCode } from "@/lib/actions/my-jobs";
import type { PreferencesActionErrorCode } from "@/lib/actions/preferences";

type TFn = ReturnType<typeof useLingui>["t"];

export type ActionErrorCode =
  | BillingActionErrorCode
  | MyJobsActionErrorCode
  | PreferencesActionErrorCode;

export function translateActionError(t: TFn, code: ActionErrorCode): string {
  switch (code) {
    case "not_authenticated":
      return t({ id: "actions.error.notAuthenticated", comment: "Generic server-action error shown when a user is not logged in", message: "Please log in and try again." });
    case "not_found":
      return t({ id: "actions.error.notFound", comment: "Generic server-action error shown when an item cannot be found", message: "Not found." });
    case "payments_unavailable":
      return t({ id: "actions.error.paymentsUnavailable", comment: "Billing error when checkout is not configured yet", message: "Payments are not available yet." });
    case "billing_account_not_found":
      return t({ id: "actions.error.billingAccountNotFound", comment: "Billing error when the user has no billing account", message: "No billing account found." });
    case "billing_portal_unavailable":
      return t({ id: "actions.error.billingPortalUnavailable", comment: "Billing error when the portal is not configured yet", message: "Billing portal is not available yet." });
    case "password_set_failed":
      return t({ id: "actions.error.passwordSetFailed", comment: "Generic error when setting an account password fails", message: "Failed to set password." });
    case "username_length":
      return t({ id: "actions.error.usernameLength", comment: "Username validation error for length", message: "Username must be 3-30 characters." });
    case "username_invalid_characters":
      return t({ id: "actions.error.usernameInvalidCharacters", comment: "Username validation error for unsupported characters", message: "Username can only use lowercase letters, numbers, and hyphens, and cannot start or end with a hyphen." });
    case "username_reserved":
      return t({ id: "actions.error.usernameReserved", comment: "Username validation error when a reserved username is requested", message: "This username is reserved." });
    case "username_update_failed":
      return t({ id: "actions.error.usernameUpdateFailed", comment: "Generic error when updating an account username fails", message: "Could not update username." });
    case "user_not_found":
      return t({ id: "actions.error.userNotFound", comment: "Generic account error when the current user row is missing", message: "User not found." });
    case "invalid_status_transition":
      return t({ id: "actions.error.invalidStatusTransition", comment: "My Jobs error when an application status transition is not allowed", message: "That status change is not allowed." });
    case "interview_round_failed":
      return t({ id: "actions.error.interviewRoundFailed", comment: "My Jobs error when assigning an interview round fails", message: "Could not assign interview round." });
  }
}
