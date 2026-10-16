import { createHash } from "node:crypto";
import { readAiFilterRuntimePolicy } from "./policy";

/** Sensitive Vercel variables may be omitted or empty when pulled by CI. */
export function narrowedHostExecutionState(sourcePaused: boolean, env: Partial<NodeJS.ProcessEnv> = process.env): "enabled" | "disabled" {
  if (sourcePaused) return "disabled";
  const flags = [env.AI_FILTER_ENABLED, env.AI_FILTER_JEV_1_13_0_ENABLED];
  if (flags.some(value => value !== "true" && value !== "false")) {
    throw new Error("Narrowed execution flags must be readable literal booleans");
  }
  return flags.includes("false") ? "disabled" : "enabled";
}

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
