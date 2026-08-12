import { describe, expect, it, vi } from "vitest";

import {
  sanitizeTypesenseClientBoundary,
} from "../typesense-client";
import {
  isRetryableError,
  isTypesenseRateLimitError,
  isTypesenseUnavailableError,
} from "../typesense-retry";

function credentialedError(overrides: Record<string, unknown> = {}) {
  return {
    name: "AxiosError",
    message: "timeout SECRET_CANARY_MESSAGE",
    code: "ECONNABORTED",
    status: 0,
    config: {
      headers: { "X-TYPESENSE-API-KEY": "SECRET_CANARY_HEADER" },
    },
    request: { responseURL: "https://SECRET_CANARY_URL.example" },
    ...overrides,
  };
}

describe("sanitizeTypesenseClientBoundary", () => {
  it("sanitizes a terminal rejection reached through the fluent SDK shape", async () => {
    const raw = credentialedError();
    const search = vi.fn().mockRejectedValue(raw);
    const client = sanitizeTypesenseClientBoundary({
      collections(name: string) {
        return {
          documents() {
            return { search, name };
          },
        };
      },
    });

    const thrown = await client
      .collections("company")
      .documents()
      .search()
      .catch((err: unknown) => err);

    expect(search).toHaveBeenCalledOnce();
    expect(thrown).toBeInstanceOf(Error);
    expect(thrown).not.toBe(raw);
    expect(thrown.message).toBe("Typesense request failed");
    expect(thrown).not.toHaveProperty("config");
    expect(thrown).not.toHaveProperty("request");
    expect(JSON.stringify(thrown)).not.toContain("SECRET_CANARY");
    expect(isRetryableError(thrown)).toBe(true);
    expect(isTypesenseUnavailableError(thrown)).toBe(true);
  });

  it("keeps a real 429 authoritative while discarding its response body", async () => {
    const raw = credentialedError({
      response: { status: 429, data: "SECRET_CANARY_BODY" },
    });
    const client = sanitizeTypesenseClientBoundary({
      collections: () => ({
        documents: () => ({ search: () => Promise.reject(raw) }),
      }),
    });

    const thrown = await client
      .collections()
      .documents()
      .search()
      .catch((err: unknown) => err);

    expect(isTypesenseRateLimitError(thrown)).toBe(true);
    expect(isRetryableError(thrown)).toBe(false);
    expect(isTypesenseUnavailableError(thrown)).toBe(false);
    expect(JSON.stringify(thrown)).not.toContain("SECRET_CANARY");
  });

  it("preserves message-only Typesense rate-limit classification safely", async () => {
    const raw = credentialedError({
      code: undefined,
      message: "Request failed with HTTP code 429 SECRET_CANARY_MESSAGE",
    });
    const client = sanitizeTypesenseClientBoundary({
      collections: () => ({
        documents: () => ({ search: () => Promise.reject(raw) }),
      }),
    });

    const thrown = await client
      .collections()
      .documents()
      .search()
      .catch((err: unknown) => err);

    expect(isTypesenseRateLimitError(thrown)).toBe(true);
    expect(isRetryableError(thrown)).toBe(false);
    expect(isTypesenseUnavailableError(thrown)).toBe(false);
    expect(JSON.stringify(thrown)).not.toContain("SECRET_CANARY");
  });

  it("sanitizes synchronous SDK failures without changing method receivers", () => {
    const raw = credentialedError();
    const resource = {
      marker: "bound receiver",
      search() {
        expect(this.marker).toBe("bound receiver");
        throw raw;
      },
    };
    const client = sanitizeTypesenseClientBoundary({
      collections: () => ({ documents: () => resource }),
    });

    let thrown: unknown;
    try {
      client.collections().documents().search();
    } catch (err) {
      thrown = err;
    }

    expect(thrown).toBeInstanceOf(Error);
    expect(thrown).not.toBe(raw);
    expect(JSON.stringify(thrown)).not.toContain("SECRET_CANARY");
  });

  it("sanitizes failures thrown by a fluent SDK property getter", () => {
    const raw = credentialedError();
    const client = sanitizeTypesenseClientBoundary(
      Object.defineProperty({}, "collections", {
        get() {
          throw raw;
        },
      }),
    );

    let thrown: unknown;
    try {
      void (client as { collections?: unknown }).collections;
    } catch (err) {
      thrown = err;
    }

    expect(thrown).toBeInstanceOf(Error);
    expect(thrown).not.toBe(raw);
    expect(JSON.stringify(thrown)).not.toContain("SECRET_CANARY");
  });

  it("returns successful SDK results unchanged", async () => {
    const result = { found: 1, hits: [{ document: { id: "company-1" } }] };
    const client = sanitizeTypesenseClientBoundary({
      collections: () => ({
        documents: () => ({ search: () => Promise.resolve(result) }),
      }),
    });

    await expect(client.collections().documents().search()).resolves.toBe(result);
  });
});
