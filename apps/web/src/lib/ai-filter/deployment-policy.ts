import { readAiFilterRuntimePolicy } from "./policy";
import { parseStableCandidateOrderReadinessReceipt } from "../search/stable-candidate-order-readiness";

const keys = ["AI_FILTER_ENABLED", "AI_FILTER_JEV_1_13_0_ENABLED", "TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT",
  "AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS", "AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS",
  "AI_FILTER_MAX_SEGMENTS_PER_USER", "AI_FILTER_MAX_SEGMENTS_PROJECT", "STRIPE_ENVIRONMENT", "PADDLE_ENVIRONMENT"] as const;
export type NarrowedDeploymentPolicy = { version: 1; values: Record<typeof keys[number], string | null>; hmacSha256: string; typesafeSha256: string };

/** A protected runtime snapshot overrides unreadable values from Vercel pull. */
export function applyNarrowedDeploymentPolicy(raw: string | undefined, env: NodeJS.ProcessEnv = process.env): NarrowedDeploymentPolicy | null {
  if (!raw) return null;
  if (Buffer.byteLength(raw) > 32768) throw new Error("Invalid protected Narrowed policy");
  const policy = JSON.parse(raw) as NarrowedDeploymentPolicy;
  if (!policy || policy.version !== 1 || Object.keys(policy).sort().join() !== "hmacSha256,typesafeSha256,values,version" ||
    !/^[a-f0-9]{64}$/.test(policy.hmacSha256) || !/^[a-f0-9]{64}$/.test(policy.typesafeSha256) ||
    !policy.values || Object.keys(policy.values).sort().join() !== [...keys].sort().join()) throw new Error("Invalid protected Narrowed policy");
  const next = { ...env };
  for (const key of keys) {
    const value = policy.values[key];
    if (value !== null && typeof value !== "string") throw new Error("Invalid protected Narrowed policy");
    if (value === null) delete next[key]; else next[key] = value;
  }
  if (!["true", "false"].includes(next.AI_FILTER_ENABLED ?? "") || !["true", "false"].includes(next.AI_FILTER_JEV_1_13_0_ENABLED ?? "") ||
    !parseStableCandidateOrderReadinessReceipt(next.TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT)) throw new Error("Invalid protected Narrowed policy");
  readAiFilterRuntimePolicy(next);
  for (const key of keys) { if (next[key] === undefined) delete env[key]; else env[key] = next[key]; }
  return policy;
}
