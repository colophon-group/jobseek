import { describe, expect, it } from "vitest";
import { PRODUCT_NEWS_CONSENT_VERSION, productNewsSignupChoice } from "../policy";

describe("signup product-news consent", () => {
  it("requires an explicit boolean choice for this wording on email signup", () => {
    const body = { productNews: true, productNewsConsentVersion: PRODUCT_NEWS_CONSENT_VERSION };
    expect(productNewsSignupChoice("/sign-up/email", body)).toBe(true);
    for (const value of [false, undefined, "true", 1, {}, null]) {
      expect(productNewsSignupChoice("/sign-up/email", { ...body, productNews: value })).toBe(false);
    }
    expect(productNewsSignupChoice("/sign-up/email", { ...body, productNewsConsentVersion: "old" })).toBe(false);
    for (const path of ["/sign-in/email", "/sign-in/social", "/callback/google", "/update-user", undefined]) {
      expect(productNewsSignupChoice(path, body)).toBe(false);
    }
  });
});
