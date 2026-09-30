import { afterEach, describe, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { stripeApiKey, stripeCheckoutEnabled, stripeSignupOpen, stripeEnvironment, billingOrigin } from "../config";
describe("Stripe environment isolation and launch gate", () => {
  afterEach(() => vi.unstubAllEnvs());
  it("requires explicit matching credentials and rejects production sandbox or live preview", () => {
    vi.stubEnv("STRIPE_ENVIRONMENT", ""); expect(() => stripeEnvironment()).toThrow();
    vi.stubEnv("STRIPE_ENVIRONMENT", "sandbox"); vi.stubEnv("STRIPE_SECRET_KEY", "sk_live_fixture"); expect(() => stripeApiKey()).toThrow();
    vi.stubEnv("STRIPE_SECRET_KEY", "sk_test_fixture"); expect(stripeApiKey()).toBe("sk_test_fixture");
    vi.stubEnv("VERCEL_ENV", "production"); expect(() => stripeEnvironment()).toThrow();
    vi.stubEnv("VERCEL_ENV", "preview"); vi.stubEnv("STRIPE_ENVIRONMENT", "production"); expect(() => stripeEnvironment()).toThrow();
  });
  it("keeps checkout disabled until all server configuration is present", () => {
    vi.stubEnv("STRIPE_ENVIRONMENT", "sandbox"); vi.stubEnv("STRIPE_SECRET_KEY", "sk_test_fixture");
    vi.stubEnv("STRIPE_PRO_PRICE_ID", "price_fixture"); vi.stubEnv("STRIPE_WEBHOOK_SECRET", "whsec_fixture");
    vi.stubEnv("BETTER_AUTH_URL", "http://localhost:3100"); vi.stubEnv("STRIPE_PORTAL_CONFIGURATION_ID", "bpc_fixture");
    vi.stubEnv("STRIPE_CHECKOUT_ENABLED", "false"); expect(stripeCheckoutEnabled()).toBe(false);
    vi.stubEnv("STRIPE_CHECKOUT_ENABLED", "true"); expect(stripeCheckoutEnabled()).toBe(true);
    vi.stubEnv("STRIPE_WEBHOOK_SECRET", ""); expect(stripeCheckoutEnabled()).toBe(false);
    vi.stubEnv("BETTER_AUTH_URL", "http://evil.test"); expect(() => billingOrigin()).toThrow();
  });
  it("publishes production signup without build-time secrets while keeping payment actions gated", () => {
    vi.stubEnv("VERCEL_ENV", "production"); vi.stubEnv("STRIPE_ENVIRONMENT", "production");
    vi.stubEnv("STRIPE_CHECKOUT_ENABLED", "true"); vi.stubEnv("STRIPE_PRO_PRICE_ID", "price_fixture");
    vi.stubEnv("STRIPE_PORTAL_CONFIGURATION_ID", "bpc_fixture"); vi.stubEnv("BETTER_AUTH_URL", "https://jseek.co");
    vi.stubEnv("STRIPE_SECRET_KEY", ""); vi.stubEnv("STRIPE_WEBHOOK_SECRET", "");
    expect(stripeSignupOpen()).toBe(true);
    expect(stripeCheckoutEnabled()).toBe(false);
    vi.stubEnv("STRIPE_SECRET_KEY", "sk_live_fixture"); vi.stubEnv("STRIPE_WEBHOOK_SECRET", "whsec_fixture");
    expect(stripeCheckoutEnabled()).toBe(true);
    vi.stubEnv("STRIPE_CHECKOUT_ENABLED", "false");
    expect(stripeSignupOpen()).toBe(false);
    expect(stripeCheckoutEnabled()).toBe(false);
  });
  it("does not publish signup with mismatched mode or incomplete public configuration", () => {
    vi.stubEnv("VERCEL_ENV", "preview"); vi.stubEnv("STRIPE_ENVIRONMENT", "production");
    vi.stubEnv("STRIPE_CHECKOUT_ENABLED", "true"); vi.stubEnv("STRIPE_PRO_PRICE_ID", "price_fixture");
    vi.stubEnv("STRIPE_PORTAL_CONFIGURATION_ID", "bpc_fixture"); vi.stubEnv("BETTER_AUTH_URL", "https://jseek.co");
    expect(stripeSignupOpen()).toBe(false);
    vi.stubEnv("VERCEL_ENV", "production"); vi.stubEnv("STRIPE_PRO_PRICE_ID", "");
    expect(stripeSignupOpen()).toBe(false);
  });
});
