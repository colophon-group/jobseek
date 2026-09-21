import { createHmac, timingSafeEqual } from "node:crypto";

import {
  CLASSIFIER_INPUT_NORMALIZER_VERSION,
  CLASSIFIER_INPUT_SCHEMA_VERSION,
  type NormalizedClassifierInputV1,
} from "./classifier-input";
import { normalizeAiFilterSoftQueryV1 } from "./contract";
import {
  AI_FILTER_CACHE_KEY_VERSION,
  AI_FILTER_PROMPT_VERSION,
  JEV_MODEL,
} from "./policy";

export type AiFilterCacheIdentity = Readonly<{
  keyVersion: typeof AI_FILTER_CACHE_KEY_VERSION;
  cacheKey: string;
  normalizedQuery: string;
  contentIdentity: string;
}>;

const SHA256_HEX = /^[0-9a-f]{64}$/;

function requireHmacSecret(secret: string): string {
  if (Buffer.byteLength(secret, "utf8") < 32) {
    throw new Error("AI filter cache HMAC secret must contain at least 32 bytes");
  }
  return secret;
}

/** Exact, versioned semantic value that is HMACed but never persisted raw. */
export function serializeAiFilterCacheSemanticInput(input: {
  queryText: unknown;
  classifierInput: NormalizedClassifierInputV1;
}): string {
  if (!SHA256_HEX.test(input.classifierInput.contentIdentity)) {
    throw new TypeError("classifier content identity is invalid");
  }
  const normalizedQuery = normalizeAiFilterSoftQueryV1(input.queryText);
  return JSON.stringify({
    keyVersion: AI_FILTER_CACHE_KEY_VERSION,
    model: JEV_MODEL,
    promptVersion: AI_FILTER_PROMPT_VERSION,
    schemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    normalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    query: normalizedQuery,
    job: input.classifierInput.payload,
    contentIdentity: input.classifierInput.contentIdentity,
  });
}

export function buildAiFilterCacheIdentity(input: {
  queryText: unknown;
  classifierInput: NormalizedClassifierInputV1;
  hmacSecret: string;
}): AiFilterCacheIdentity {
  const semanticInput = serializeAiFilterCacheSemanticInput(input);
  const cacheKey = createHmac("sha256", requireHmacSecret(input.hmacSecret))
    .update(semanticInput, "utf8")
    .digest("hex");
  return Object.freeze({
    keyVersion: AI_FILTER_CACHE_KEY_VERSION,
    cacheKey,
    normalizedQuery: normalizeAiFilterSoftQueryV1(input.queryText),
    contentIdentity: input.classifierInput.contentIdentity,
  });
}

export function aiFilterCacheKeysEqual(left: string, right: string): boolean {
  if (!SHA256_HEX.test(left) || !SHA256_HEX.test(right)) return false;
  return timingSafeEqual(Buffer.from(left, "hex"), Buffer.from(right, "hex"));
}
