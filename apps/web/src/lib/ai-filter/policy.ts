/** Production policy for the direct TypeSafe Jev route. */

export const JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone" as const;
export const JEV_MODEL = "jev-1.13.0" as const;
export const JEV_BATCH_SIZE = 5;
export const JEV_TIMEOUT_MS = 10_000;
export const JEV_MAX_RETRIES = 1;

export const AI_FILTER_PROMPT_VERSION = "jev-job-fit-choice-v1" as const;
export const AI_FILTER_CACHE_KEY_VERSION = "ai-filter-cache-hmac-v1" as const;
export const AI_FILTER_PRICE_VERSION = "typesafe-2026-09-15" as const;

/** TypeSafe lists Jev at $0.042 per million input tokens. */
export const JEV_INPUT_PRICE_NANODOLLARS_PER_TOKEN = 42;
export const JEV_OUTPUT_PRICE_NANODOLLARS_PER_TOKEN = 0;

/**
 * Bounded reservation input. The live 5-job, 12k-character fixture used
 * 11,232 input tokens. The 80k ceiling also covers worst-case multilingual
 * tokenization of five 12k-code-point descriptions while still leaving less
 * than one cent of possible monthly-budget headroom.
 */
export const JEV_MAX_INPUT_TOKENS_PER_CALL = 80_000;
export const JEV_MAX_CALL_RESERVATION_NANODOLLARS =
  JEV_MAX_INPUT_TOKENS_PER_CALL *
  JEV_INPUT_PRICE_NANODOLLARS_PER_TOKEN *
  (JEV_MAX_RETRIES + 1);

export const AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS = 10_000_000_000;

/** Observed direct-API bounds from the maintained 5-job synthetic fixtures. */
export const JEV_COMPACT_DECISION_ESTIMATE_NANODOLLARS = 14_373;
export const JEV_MAX_DESCRIPTION_DECISION_ESTIMATE_NANODOLLARS = 69_166;

export type AiFilterRuntimePolicy = Readonly<{
  enabled: boolean;
  routeEnabled: boolean;
  userMonthlyBudgetNanodollars: number;
  projectMonthlyBudgetNanodollars: number;
  maxSegmentsPerUser: number;
  maxSegmentsPerProject: number;
}>;

function readPositiveSafeInteger(name: string, value: string | undefined): number {
  if (!value || !/^[1-9]\d*$/.test(value)) {
    throw new Error(`${name} must be a positive integer`);
  }
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) {
    throw new Error(`${name} must be a safe integer`);
  }
  return parsed;
}

/**
 * Execution fails closed until both feature switches and a project budget are
 * explicitly configured. Reads may still expose persisted paused state.
 */
export function readAiFilterRuntimePolicy(
  env: Readonly<Record<string, string | undefined>> = process.env,
): AiFilterRuntimePolicy {
  return Object.freeze({
    enabled: env.AI_FILTER_ENABLED === "true",
    routeEnabled: env.AI_FILTER_JEV_1_13_0_ENABLED === "true",
    userMonthlyBudgetNanodollars: AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS,
    projectMonthlyBudgetNanodollars: readPositiveSafeInteger(
      "AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS",
      env.AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS,
    ),
    maxSegmentsPerUser: env.AI_FILTER_MAX_SEGMENTS_PER_USER
      ? readPositiveSafeInteger(
          "AI_FILTER_MAX_SEGMENTS_PER_USER",
          env.AI_FILTER_MAX_SEGMENTS_PER_USER,
        )
      : 2,
    maxSegmentsPerProject: env.AI_FILTER_MAX_SEGMENTS_PROJECT
      ? readPositiveSafeInteger(
          "AI_FILTER_MAX_SEGMENTS_PROJECT",
          env.AI_FILTER_MAX_SEGMENTS_PROJECT,
        )
      : 20,
  });
}

export function jevCostNanodollars(inputTokens: number, outputTokens: number): number {
  if (
    !Number.isSafeInteger(inputTokens) ||
    inputTokens < 0 ||
    !Number.isSafeInteger(outputTokens) ||
    outputTokens < 0
  ) {
    throw new RangeError("Jev token usage must contain non-negative safe integers");
  }
  const cost =
    inputTokens * JEV_INPUT_PRICE_NANODOLLARS_PER_TOKEN +
    outputTokens * JEV_OUTPUT_PRICE_NANODOLLARS_PER_TOKEN;
  if (!Number.isSafeInteger(cost)) {
    throw new RangeError("Jev cost exceeds fixed-point range");
  }
  return cost;
}
