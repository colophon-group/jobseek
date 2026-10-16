import { createHash } from "node:crypto";
import { readAiFilterRuntimePolicy } from "./policy";

/** Bind promotion to matching code and the current non-secret execution policy. */
export function narrowedWorkerReadinessKey(bundleHash: string, env: NodeJS.ProcessEnv = process.env) {
  const policy = {
    execution: readAiFilterRuntimePolicy(env),
    stripe: env.STRIPE_ENVIRONMENT ?? null,
    paddle: env.PADDLE_ENVIRONMENT ?? null,
  };
  const digest = createHash("sha256").update(JSON.stringify(policy)).digest("hex");
  return `jobseek:narrowed:${bundleHash}:${digest}`;
}
