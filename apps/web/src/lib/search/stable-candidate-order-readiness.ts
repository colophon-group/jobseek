import { WATCHLIST_CANDIDATE_ORDER_KEY_VERSION } from "@/lib/search/watchlist-candidate-query";

export const STABLE_CANDIDATE_ORDER_RECEIPT_ENV =
  "TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT" as const;
export const STABLE_CANDIDATE_ORDER_RECEIPT_SCHEMA =
  "typesense-stable-candidate-order-readiness-v1" as const;

export type StableCandidateOrderReadinessReceipt = {
  authoritativeCount: number;
  benchmarkSha256: string;
  completedAt: string;
  keyVersion: typeof WATCHLIST_CANDIDATE_ORDER_KEY_VERSION;
  partitions: 256;
  reconciliationRunId: string;
  schemaVersion: typeof STABLE_CANDIDATE_ORDER_RECEIPT_SCHEMA;
  unresolved: 0;
};

const RECEIPT_KEYS = [
  "authoritativeCount",
  "benchmarkSha256",
  "completedAt",
  "keyVersion",
  "partitions",
  "reconciliationRunId",
  "schemaVersion",
  "unresolved",
] as const;
const LOWERCASE_UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const LOWERCASE_SHA256 = /^[0-9a-f]{64}$/;
const UTC_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;

export function parseStableCandidateOrderReadinessReceipt(
  encoded: string | undefined,
): StableCandidateOrderReadinessReceipt | null {
  if (!encoded || !/^[A-Za-z0-9_-]+$/.test(encoded)) return null;
  let decoded: string;
  try {
    const bytes = Buffer.from(encoded, "base64url");
    if (bytes.toString("base64url") !== encoded) return null;
    decoded = bytes.toString("utf8");
  } catch {
    return null;
  }

  let value: unknown;
  try {
    value = JSON.parse(decoded);
  } catch {
    return null;
  }
  if (value === null || Array.isArray(value) || typeof value !== "object") {
    return null;
  }
  const receipt = value as Record<string, unknown>;
  if (
    Object.keys(receipt).sort().join("\0") !==
    [...RECEIPT_KEYS].sort().join("\0")
  ) {
    return null;
  }
  if (
    receipt.schemaVersion !== STABLE_CANDIDATE_ORDER_RECEIPT_SCHEMA ||
    receipt.keyVersion !== WATCHLIST_CANDIDATE_ORDER_KEY_VERSION ||
    receipt.partitions !== 256 ||
    receipt.unresolved !== 0 ||
    !Number.isSafeInteger(receipt.authoritativeCount) ||
    (receipt.authoritativeCount as number) < 0 ||
    typeof receipt.reconciliationRunId !== "string" ||
    !LOWERCASE_UUID.test(receipt.reconciliationRunId) ||
    typeof receipt.completedAt !== "string" ||
    !UTC_TIMESTAMP.test(receipt.completedAt) ||
    !Number.isFinite(Date.parse(receipt.completedAt)) ||
    typeof receipt.benchmarkSha256 !== "string" ||
    !LOWERCASE_SHA256.test(receipt.benchmarkSha256)
  ) {
    return null;
  }
  return receipt as StableCandidateOrderReadinessReceipt;
}

export function stableCandidateOrderReady(): boolean {
  return parseStableCandidateOrderReadinessReceipt(
    process.env[STABLE_CANDIDATE_ORDER_RECEIPT_ENV],
  ) !== null;
}
