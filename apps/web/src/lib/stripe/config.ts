import "server-only";

export function stripeEnvironment(): "sandbox" | "production" {
  const value = process.env.STRIPE_ENVIRONMENT;
  if (value !== "sandbox" && value !== "production") throw new Error("Set STRIPE_ENVIRONMENT explicitly");
  if (process.env.VERCEL_ENV === "production" && value !== "production") throw new Error("Production cannot use Stripe sandbox");
  if (process.env.VERCEL_ENV === "preview" && value !== "sandbox") throw new Error("Preview cannot use live Stripe");
  return value;
}

export function stripeApiKey() {
  const key = process.env.STRIPE_SECRET_KEY;
  const mode = stripeEnvironment() === "sandbox" ? "test" : "live";
  if (!key || !new RegExp(`^(sk|rk)_${mode}_`).test(key)) throw new Error("Stripe key/environment mismatch");
  return key;
}

export function stripePriceId() {
  const value = process.env.STRIPE_PRO_PRICE_ID;
  if (!value?.startsWith("price_")) throw new Error("Set STRIPE_PRO_PRICE_ID");
  return value;
}

export function stripePortalConfigurationId() {
  const value = process.env.STRIPE_PORTAL_CONFIGURATION_ID;
  if (!value?.startsWith("bpc_")) throw new Error("Set STRIPE_PORTAL_CONFIGURATION_ID");
  return value;
}

export function billingOrigin() {
  const url = new URL(process.env.BETTER_AUTH_URL ?? "");
  if (url.protocol !== "https:" && !(stripeEnvironment() === "sandbox" && url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname))) {
    throw new Error("Billing requires HTTPS or local sandbox");
  }
  return url.origin;
}

export function stripeCheckoutEnabled() {
  if (process.env.STRIPE_CHECKOUT_ENABLED !== "true") return false;
  try {
    stripeApiKey(); stripePriceId(); billingOrigin(); stripePortalConfigurationId();
    return Boolean(process.env.STRIPE_WEBHOOK_SECRET?.startsWith("whsec_"));
  } catch { return false; }
}
