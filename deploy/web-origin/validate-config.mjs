import { readFileSync } from "node:fs";

const path = process.argv[2];
if (!path) throw new Error("runtime configuration path required");

const config = JSON.parse(readFileSync(path, "utf8"));
if (!config || typeof config !== "object" || !config.env || typeof config.env !== "object" || Array.isArray(config.env)) {
  throw new Error("invalid web origin runtime configuration");
}

for (const [key, value] of Object.entries(config.env)) {
  if (!/^[A-Z][A-Z0-9_]*$/.test(key) || typeof value !== "string" || value.includes("\0")) {
    throw new Error(`invalid web origin runtime key: ${key}`);
  }
}

for (const key of [
  "VERCEL_TOKEN", "CLOUDFLARE_API_TOKEN", "HETZNER_API_TOKEN",
  "DATABASE_URL_UNPOOLED", "GHCR_PAT",
]) {
  if (key in config.env) throw new Error(`provider credential forbidden in web runtime: ${key}`);
}

const required = [
  "DATABASE_URL", "BETTER_AUTH_URL", "BETTER_AUTH_SECRET", "TRUSTED_ORIGINS",
  "NEXT_SERVER_ACTIONS_ENCRYPTION_KEY", "TYPESENSE_HOST", "TYPESENSE_SEARCH_KEY",
  "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN", "R2_DOMAIN_URL",
  "RESEND_API_KEY", "RESEND_WEBHOOK_SECRET", "CRON_SECRET", "INDEXNOW_KEY",
  "JOB_ALERTS_MODE", "JOB_ALERTS_UNSUBSCRIBE_SECRET",
  "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "PADDLE_API_KEY", "PADDLE_WEBHOOK_SECRET",
];
const missing = required.filter((key) => !config.env[key]);
if (missing.length) throw new Error(`missing web origin runtime keys: ${missing.join(", ")}`);
if (config.env.BETTER_AUTH_URL !== "https://jseek.co") throw new Error("production auth origin must be jseek.co");
if (config.env.JOBSEEK_WEB_ORIGIN !== "cloudflare_tunnel") throw new Error("web origin must use the Cloudflare tunnel ingress");
if (config.env.TRUSTED_ORIGINS !== "https://jseek.co") throw new Error("production auth must trust only jseek.co");
if (config.env.VERCEL_ENV !== "production" || config.env.VERCEL) throw new Error("production guard environment is invalid");
if (!/^[A-Za-z0-9+/]{43}=$/.test(config.env.NEXT_SERVER_ACTIONS_ENCRYPTION_KEY) ||
    Buffer.from(config.env.NEXT_SERVER_ACTIONS_ENCRYPTION_KEY, "base64").length !== 32) {
  throw new Error("Server Action key must encode 32 bytes");
}

console.log("web_origin_runtime_config_valid");
