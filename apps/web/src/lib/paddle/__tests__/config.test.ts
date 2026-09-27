import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { paddleCheckoutEnabled, paddleEnvironment } from "../config";

describe("Paddle environment separation", () => {
  beforeEach(() => {
    vi.stubEnv("PADDLE_CHECKOUT_ENABLED", "true");
    vi.stubEnv("PADDLE_ENVIRONMENT", "sandbox");
    vi.stubEnv("PADDLE_API_KEY", "pdl_sdbx_fixture");
    vi.stubEnv("PADDLE_PRO_PRICE_ID", `pri_${"a".repeat(26)}`);
    vi.stubEnv("PADDLE_PRO_RETURNING_PRICE_ID", `pri_${"b".repeat(26)}`);
    vi.stubEnv("NEXT_PUBLIC_PADDLE_ENVIRONMENT", "sandbox");
    vi.stubEnv("NEXT_PUBLIC_PADDLE_CLIENT_TOKEN", "test_fixture");
    vi.stubEnv("PADDLE_WEBHOOK_SECRET", "fixture");
    vi.stubEnv("VERCEL_ENV", "development");
  });
  afterEach(() => vi.unstubAllEnvs());
  it("enables only complete, explicitly selected configuration", () => {
    expect(paddleCheckoutEnabled()).toBe(true);
    vi.stubEnv("PADDLE_WEBHOOK_SECRET", "");
    expect(paddleCheckoutEnabled()).toBe(false);
  });
  it.each([
    ["PADDLE_API_KEY", "pdl_live_fixture"],
    ["NEXT_PUBLIC_PADDLE_CLIENT_TOKEN", "live_fixture"],
    ["NEXT_PUBLIC_PADDLE_ENVIRONMENT", "production"],
    ["PADDLE_CHECKOUT_ENABLED", "false"],
    ["PADDLE_PRO_RETURNING_PRICE_ID", `pri_${"a".repeat(26)}`],
  ])("rejects mismatched %s", (key, value) => {
    vi.stubEnv(key, value);
    expect(paddleCheckoutEnabled()).toBe(false);
  });
  it("never permits sandbox access in a production deployment", () => {
    vi.stubEnv("VERCEL_ENV", "production");
    expect(paddleCheckoutEnabled()).toBe(false);
    expect(() => paddleEnvironment()).toThrow();
  });
});
