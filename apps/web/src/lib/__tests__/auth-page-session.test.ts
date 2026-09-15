import { describe, expect, it } from "vitest";

import { shouldRedirectSignedInUser } from "../auth-page-session";

describe("shouldRedirectSignedInUser", () => {
  it("redirects when the real session and client bootstrap hint agree", () => {
    expect(shouldRedirectSignedInUser(true, "logged_in=1")).toBe(true);
  });

  it("keeps a real legacy session with a missing hint on the repair surface", () => {
    expect(shouldRedirectSignedInUser(true, "other=1")).toBe(false);
  });

  it("does not trust a hint without a real session", () => {
    expect(shouldRedirectSignedInUser(false, "logged_in=1")).toBe(false);
  });

  it("does not accept a cookie-name substring", () => {
    expect(shouldRedirectSignedInUser(true, "not_logged_in=1")).toBe(false);
  });
});
