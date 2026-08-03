import { describe, expect, it, vi } from "vitest";

import {
  EXTERNAL_CLIENT_LOG_EVENT,
  logExternalError,
  safeExternalError,
} from "@/lib/safe-external-error";

const CANARIES = [
  "SECRET_CANARY_HEADER_6123",
  "SECRET_CANARY_AUTH_6123",
  "SECRET_CANARY_BODY_6123",
  "SECRET_CANARY_CAUSE_6123",
  "SECRET_CANARY_QUERY_6123",
  "SECRET_CANARY_STACK_6123",
];

function credentialedAxiosError(): unknown {
  const error = new Error(`request failed ${CANARIES[5]}`) as Error & Record<string, unknown>;
  error.stack = `stack ${CANARIES[5]}`;
  error.code = "ECONNABORTED";
  error.config = {
    auth: { username: "admin", password: CANARIES[1] },
    headers: {
      Authorization: `Bearer ${CANARIES[0]}`,
      "X-TYPESENSE-API-KEY": CANARIES[0],
    },
    baseURL: "https://admin:password@typesense.example.com",
    url: `/collections/${CANARIES[0]}/documents?x-typesense-api-key=${CANARIES[4]}`,
    data: { token: CANARIES[2] },
  };
  error.response = {
    status: 504,
    headers: {
      "x-request-id": "req-safe-123",
      authorization: CANARIES[0],
    },
    data: { secret: CANARIES[2] },
  };
  error.cause = {
    message: CANARIES[3],
    headers: { cookie: CANARIES[3] },
    body: CANARIES[2],
  };
  return error;
}

describe("safeExternalError", () => {
  it("retains only allowlisted operational fields", () => {
    expect(
      safeExternalError(credentialedAxiosError(), {
        service: "typesense",
        operation: "search_jobs",
        retryCount: 2,
      }),
    ).toEqual({
      event: EXTERNAL_CLIENT_LOG_EVENT,
      service: "typesense",
      operation: "search_jobs",
      kind: "timeout",
      timeout: true,
      status: 504,
      code: "ECONNABORTED",
      retry_count: 2,
      request_id: "req-safe-123",
      host: "typesense.example.com",
      path: "/collections/[redacted]/documents",
    });
  });

  it("never serializes secrets from nested SDK error shapes", () => {
    const serialized = JSON.stringify(
      safeExternalError(credentialedAxiosError(), {
        service: "typesense",
        operation: "search_jobs",
      }),
    );

    for (const canary of CANARIES) expect(serialized).not.toContain(canary);
    expect(serialized).not.toMatch(/authorization|api[-_]?key|password|bearer/i);
  });

  it("does not trust arbitrary codes, request IDs, operations, or throwing getters", () => {
    const hostile = Object.create(null, {
      code: { get: () => "SECRET_CODE" },
      status: { get: () => 401 },
      message: { get: () => { throw new Error("SECRET_GETTER"); } },
      headers: { value: { "x-request-id": "SECRET_TOKEN_VALUE" } },
    });

    expect(
      safeExternalError(hostile, {
        service: "github",
        operation: "bad operation with secrets",
      }),
    ).toEqual({
      event: EXTERNAL_CLIENT_LOG_EVENT,
      service: "github",
      operation: "unknown",
      kind: "auth",
      timeout: false,
      status: 401,
    });
  });

  it("logs only the sanitized envelope", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    logExternalError(
      "error",
      { service: "redis", operation: "session_cache_scan" },
      credentialedAxiosError(),
    );

    expect(spy).toHaveBeenCalledWith(
      EXTERNAL_CLIENT_LOG_EVENT,
      expect.objectContaining({ service: "redis", operation: "session_cache_scan" }),
    );
    expect(JSON.stringify(spy.mock.calls)).not.toContain("SECRET_CANARY");
    spy.mockRestore();
  });
});
