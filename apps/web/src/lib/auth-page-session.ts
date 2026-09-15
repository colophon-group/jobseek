import { hasCookieNamed, LOGGED_IN_COOKIE } from "@/lib/client-cookies";

/**
 * Keep a real session with a missing client bootstrap hint on the auth page.
 * Submitting the form runs Better Auth's after hook and recreates the hint,
 * repairing the split state without asking the user to clear cookies.
 */
export function shouldRedirectSignedInUser(
  hasSession: boolean,
  cookieHeader: string,
): boolean {
  return hasSession && hasCookieNamed(cookieHeader, LOGGED_IN_COOKIE);
}
