import type { useLingui } from "@lingui/react/macro";
import { msg } from "@lingui/core/macro";
import type { MessageDescriptor } from "@lingui/core";
import type { BillingActionErrorCode } from "@/lib/actions/billing";
import type { MyJobsActionErrorCode } from "@/lib/actions/my-jobs";
import type { PreferencesActionErrorCode } from "@/lib/actions/preferences";

type TFn = ReturnType<typeof useLingui>["t"];

export type ActionErrorCode =
  | BillingActionErrorCode
  | MyJobsActionErrorCode
  | PreferencesActionErrorCode;

const actionErrorMessages = {
  not_authenticated: msg({ id: "actions.error.notAuthenticated", comment: "Generic server-action error shown when a user is not logged in", message: "Please log in and try again." }),
  not_found: msg({ id: "actions.error.notFound", comment: "Generic server-action error shown when an item cannot be found", message: "Not found." }),
  payments_unavailable: msg({ id: "actions.error.paymentsUnavailable", comment: "Billing error when checkout is not configured yet", message: "Payments are not available yet." }),
  billing_account_not_found: msg({ id: "actions.error.billingAccountNotFound", comment: "Billing error when the user has no billing account", message: "No billing account found." }),
  billing_portal_unavailable: msg({ id: "actions.error.billingPortalUnavailable", comment: "Billing error when the portal is not configured yet", message: "Billing portal is not available yet." }),
  password_set_failed: msg({ id: "actions.error.passwordSetFailed", comment: "Generic error when setting an account password fails", message: "Failed to set password." }),
  username_length: msg({ id: "actions.error.usernameLength", comment: "Username validation error for length", message: "Username must be 3-30 characters." }),
  username_invalid_characters: msg({ id: "actions.error.usernameInvalidCharacters", comment: "Username validation error for unsupported characters", message: "Username can only use lowercase letters, numbers, and hyphens, and cannot start or end with a hyphen." }),
  username_reserved: msg({ id: "actions.error.usernameReserved", comment: "Username validation error when a reserved username is requested", message: "This username is reserved." }),
  username_update_failed: msg({ id: "actions.error.usernameUpdateFailed", comment: "Generic error when updating an account username fails", message: "Could not update username." }),
  user_not_found: msg({ id: "actions.error.userNotFound", comment: "Generic account error when the current user row is missing", message: "User not found." }),
  invalid_status_transition: msg({ id: "actions.error.invalidStatusTransition", comment: "My Jobs error when an application status transition is not allowed", message: "That status change is not allowed." }),
  interview_round_failed: msg({ id: "actions.error.interviewRoundFailed", comment: "My Jobs error when assigning an interview round fails", message: "Could not assign interview round." }),
} satisfies Record<ActionErrorCode, MessageDescriptor>;

export function translateActionError(t: TFn, code: ActionErrorCode): string {
  return t(actionErrorMessages[code]);
}
