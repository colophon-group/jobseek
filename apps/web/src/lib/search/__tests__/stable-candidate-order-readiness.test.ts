import { describe, expect, it } from "vitest";

import { parseStableCandidateOrderReadinessReceipt } from "../stable-candidate-order-readiness";

const VALID_PAYLOAD = {
  authoritativeCount: 123_456,
  benchmarkSha256: "a".repeat(64),
  completedAt: "2026-09-11T10:00:00.123456Z",
  keyVersion: "uuid-b64lex-v1",
  partitions: 256,
  reconciliationRunId: "00000000-0000-0000-0000-000000000001",
  schemaVersion: "typesense-stable-candidate-order-readiness-v1",
  unresolved: 0,
};

function encode(value: object): string {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

describe("stable candidate order readiness receipt", () => {
  it("accepts the exact durable reconciliation and benchmark contract", () => {
    expect(parseStableCandidateOrderReadinessReceipt(
      encode(VALID_PAYLOAD),
    )).toEqual(VALID_PAYLOAD);
  });

  it.each([
    ["legacy boolean", "1"],
    ["partial coverage", encode({ ...VALID_PAYLOAD, partitions: 255 })],
    ["unresolved drift", encode({ ...VALID_PAYLOAD, unresolved: 1 })],
    ["wrong key version", encode({ ...VALID_PAYLOAD, keyVersion: "uuid-copy-v0" })],
    ["missing benchmark", encode({ ...VALID_PAYLOAD, benchmarkSha256: undefined })],
    ["extra unreviewed field", encode({ ...VALID_PAYLOAD, ready: true })],
  ])("rejects %s", (_case, receipt) => {
    expect(parseStableCandidateOrderReadinessReceipt(receipt)).toBeNull();
  });
});
