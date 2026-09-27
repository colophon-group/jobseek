import "server-only";

export function paddleEnvironment(): "sandbox" | "production" {
  const value = process.env.PADDLE_ENVIRONMENT;
  if (value !== "sandbox" && value !== "production") {
    throw new Error("PADDLE_ENVIRONMENT must explicitly be sandbox or production");
  }
  if (process.env.VERCEL_ENV === "production" && value !== "production") {
    throw new Error("Production deployments cannot use Paddle sandbox");
  }
  return value;
}

export function paddlePriceIds() {
  const trial = process.env.PADDLE_PRO_PRICE_ID;
  const returning = process.env.PADDLE_PRO_RETURNING_PRICE_ID;
  if (!trial || !returning || !/^pri_[a-z0-9]{26}$/.test(trial) ||
      !/^pri_[a-z0-9]{26}$/.test(returning) || trial === returning) {
    throw new Error("Configure distinct Paddle trial and returning-subscriber prices");
  }
  return { trial, returning };
}

export function paddleApiKey() {
  const environment = paddleEnvironment();
  const key = process.env.PADDLE_API_KEY;
  const prefix = environment === "sandbox" ? "pdl_sdbx_" : "pdl_live_";
  if (!key?.startsWith(prefix)) throw new Error("Paddle API key/environment mismatch");
  return key;
}

export function paddleCheckoutEnabled(): boolean {
  if (process.env.PADDLE_CHECKOUT_ENABLED !== "true") return false;
  try {
    const environment = paddleEnvironment();
    paddlePriceIds();
    paddleApiKey();
    return Boolean(process.env.PADDLE_WEBHOOK_SECRET) &&
      process.env.NEXT_PUBLIC_PADDLE_ENVIRONMENT === environment &&
      Boolean(process.env.NEXT_PUBLIC_PADDLE_CLIENT_TOKEN?.startsWith(
        environment === "sandbox" ? "test_" : "live_",
      ));
  } catch {
    return false;
  }
}
