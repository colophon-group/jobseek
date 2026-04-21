import { describe, expect, it } from "vitest";
import { hasCookieNamed, LOGGED_IN_COOKIE } from "../client-cookies";

describe("hasCookieNamed", () => {
  const SESSION = "better-auth.session_token";
  const SECURE_SESSION = "__Secure-better-auth.session_token";

  it("returns false for empty cookie header", () => {
    expect(hasCookieNamed("", SESSION)).toBe(false);
  });

  it("returns false when the cookie is not present", () => {
    expect(hasCookieNamed("utm_source=google; utm_medium=cpc", SESSION)).toBe(
      false,
    );
  });

  it("finds the dev-mode cookie name", () => {
    expect(hasCookieNamed(`${SESSION}=abc123`, SESSION)).toBe(true);
  });

  it("finds the prod-mode __Secure- cookie name", () => {
    expect(hasCookieNamed(`${SECURE_SESSION}=xyz`, SECURE_SESSION)).toBe(true);
  });

  it("treats an empty value as still present", () => {
    // Cookie-bomb defense: even `name=` counts as the cookie existing.
    expect(hasCookieNamed(`${SESSION}=`, SESSION)).toBe(true);
  });

  it("does NOT match when the name is only a suffix", () => {
    // Substring false-positive regression: bare `.includes` would match.
    expect(hasCookieNamed(`x_${SESSION}=val`, SESSION)).toBe(false);
  });

  it("does NOT match when the name is only a prefix", () => {
    expect(hasCookieNamed(`${SESSION}_old=val`, SESSION)).toBe(false);
  });

  it("finds the cookie when it is the first of several", () => {
    expect(hasCookieNamed(`${SESSION}=a; other=b`, SESSION)).toBe(true);
  });

  it("finds the cookie when it is in the middle", () => {
    expect(
      hasCookieNamed(`a=1; ${SECURE_SESSION}=xyz; b=2`, SECURE_SESSION),
    ).toBe(true);
  });

  it("finds the cookie when it is last", () => {
    expect(hasCookieNamed(`a=1; b=2; ${SESSION}=val`, SESSION)).toBe(true);
  });

  it("ignores leading whitespace around entries", () => {
    expect(hasCookieNamed(`a=1;   ${SESSION}=val`, SESSION)).toBe(true);
  });

  it("tolerates trailing semicolons", () => {
    expect(hasCookieNamed(`${SESSION}=val;`, SESSION)).toBe(true);
  });

  it("tolerates repeated internal semicolons", () => {
    expect(hasCookieNamed(`a=1;;; ${SESSION}=val;;;`, SESSION)).toBe(true);
  });

  it("handles a cookie value containing URL-encoded characters", () => {
    expect(hasCookieNamed(`${SESSION}=abc%20def`, SESSION)).toBe(true);
  });

  it("accepts a bare cookie name with no `=` (unusual but valid input shape)", () => {
    // Matches RFC-liberal behavior: treat `foo` alone as an implicit `foo=`.
    expect(hasCookieNamed(`${SESSION}`, SESSION)).toBe(true);
  });

  it("works with the exported LOGGED_IN_COOKIE constant", () => {
    expect(hasCookieNamed(`${LOGGED_IN_COOKIE}=1`, LOGGED_IN_COOKIE)).toBe(
      true,
    );
    expect(hasCookieNamed("other=1", LOGGED_IN_COOKIE)).toBe(false);
  });
});
