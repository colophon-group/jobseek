/**
 * Tests for `startRun` (Murmur run trigger).
 *
 * Covers the four named cases from issue #2762's Verification:
 *   1. Stub Murmur returning 200 -> returns { run_id }
 *   2. Stub returning 4xx       -> throws typed error (StartRunError)
 *   3. Bearer header included on outbound request
 *   4. Network error            -> typed timeout error
 *
 * Plus edge cases: missing env vars, malformed 2xx body, 5xx, abort/timeout,
 * pipeline-id encoding, and that the bearer token never appears in error
 * messages.
 */
import { describe, it, expect } from "vitest";

import {
  ADD_COMPANY_PIPELINE_ID,
  buildRunsUrl,
  StartRunError,
  startRun,
  type FetchImpl,
} from "./start-run";

const FAKE_URL = "https://murmur.example.test";
const FAKE_TOKEN = "test-token-do-not-log-me-0123456789ABCDEFGHIJ";

interface CapturedRequest {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string;
}

interface FakeFetchOptions {
  /** Response status to return. Default 200. */
  status?: number;
  /** Response body shape. Default `{ run_id: "run_demo_001" }`. */
  body?: unknown;
  /** If set, the fake throws to simulate transport failure. */
  throwError?: Error;
  /** If set, the fake delays this many ms before resolving. */
  delayMs?: number;
}

/**
 * Build a fake fetch + capture array. Each call appends to the array and
 * returns the configured response.
 */
function makeFakeFetch(
  options: FakeFetchOptions = {},
): { fetchImpl: FetchImpl; calls: CapturedRequest[] } {
  const calls: CapturedRequest[] = [];
  const fetchImpl: FetchImpl = async (input, init) => {
    if (options.throwError) throw options.throwError;
    const headers: Record<string, string> = {};
    if (init?.headers) {
      const h = new Headers(init.headers);
      h.forEach((v, k) => {
        headers[k] = v;
      });
    }
    calls.push({
      url: typeof input === "string" ? input : input.toString(),
      method: init?.method ?? "GET",
      headers,
      body: typeof init?.body === "string" ? init.body : "",
    });

    if (options.delayMs && options.delayMs > 0) {
      await new Promise<void>((resolve, reject) => {
        const t = setTimeout(resolve, options.delayMs);
        // Honour the abort signal so timeout tests can fire.
        const signal = init?.signal;
        if (signal) {
          if (signal.aborted) {
            clearTimeout(t);
            reject(
              Object.assign(new Error("aborted"), { name: "AbortError" }),
            );
          } else {
            signal.addEventListener("abort", () => {
              clearTimeout(t);
              reject(
                Object.assign(new Error("aborted"), { name: "AbortError" }),
              );
            });
          }
        }
      });
    }

    const status = options.status ?? 200;
    const body =
      options.body === undefined ? { run_id: "run_demo_001" } : options.body;
    const text = typeof body === "string" ? body : JSON.stringify(body);
    return new Response(text, {
      status,
      headers: { "Content-Type": "application/json" },
    });
  };
  return { fetchImpl, calls };
}

const ENV = { MURMUR_URL: FAKE_URL, MURMUR_TOKEN: FAKE_TOKEN };

describe("ADD_COMPANY_PIPELINE_ID", () => {
  it("matches the pipeline-def id used by P1 / P2", () => {
    expect(ADD_COMPANY_PIPELINE_ID).toBe("jobseek-add-company");
  });
});

describe("buildRunsUrl", () => {
  it("appends /pipelines/<id>/runs to a clean base", () => {
    expect(buildRunsUrl("https://m.example", "jobseek-add-company")).toBe(
      "https://m.example/pipelines/jobseek-add-company/runs",
    );
  });

  it("trims trailing slashes from the base", () => {
    expect(buildRunsUrl("https://m.example//", "abc")).toBe(
      "https://m.example/pipelines/abc/runs",
    );
  });

  it("URL-encodes unusual pipeline ids", () => {
    expect(buildRunsUrl("https://m.example", "weird id/with slash")).toBe(
      "https://m.example/pipelines/weird%20id%2Fwith%20slash/runs",
    );
  });
});

describe("startRun: success path (stub returning 200)", () => {
  it("returns { run_id } on 2xx with a well-formed body", async () => {
    const { fetchImpl } = makeFakeFetch({
      status: 200,
      body: { run_id: "run_abc_123" },
    });
    const result = await startRun(
      { company_name: "Acme", website: "https://acme.example" },
      { fetchImpl, env: ENV },
    );
    expect(result).toEqual({ run_id: "run_abc_123" });
  });

  it("accepts 201 / 202 as success too", async () => {
    const { fetchImpl } = makeFakeFetch({
      status: 201,
      body: { run_id: "run_201" },
    });
    const r = await startRun(
      { company_name: "X", website: "https://x.example" },
      { fetchImpl, env: ENV },
    );
    expect(r.run_id).toBe("run_201");
  });
});

describe("startRun: outbound request shape", () => {
  it("POSTs to the {pipelineId}/runs URL with the right body and bearer header", async () => {
    const { fetchImpl, calls } = makeFakeFetch({});
    await startRun(
      {
        company_name: "Acme Robotics",
        website: "https://acme.example/careers",
      },
      { fetchImpl, env: ENV },
    );
    expect(calls).toHaveLength(1);
    const call = calls[0]!;

    // Method + URL.
    expect(call.method).toBe("POST");
    expect(call.url).toBe(
      `${FAKE_URL}/pipelines/jobseek-add-company/runs`,
    );

    // Headers — bearer + content-type.
    expect(call.headers["authorization"] ?? call.headers["Authorization"]).toBe(
      `Bearer ${FAKE_TOKEN}`,
    );
    expect(
      call.headers["content-type"] ?? call.headers["Content-Type"],
    ).toContain("application/json");

    // Body shape per DESIGN.md §3.4: { initial_input: { company_name, website } }.
    const sent = JSON.parse(call.body) as {
      initial_input?: { company_name?: string; website?: string };
    };
    expect(sent.initial_input).toEqual({
      company_name: "Acme Robotics",
      website: "https://acme.example/careers",
    });
  });

  it("does NOT include the token anywhere except the Authorization header", async () => {
    const { fetchImpl, calls } = makeFakeFetch({});
    await startRun(
      { company_name: "Acme", website: "https://acme.example" },
      { fetchImpl, env: ENV },
    );
    const call = calls[0]!;
    expect(call.url.includes(FAKE_TOKEN)).toBe(false);
    expect(call.body.includes(FAKE_TOKEN)).toBe(false);
    // Body must not echo the token even via the headers map serialisation.
    const allHeaderValuesExceptAuth = Object.entries(call.headers)
      .filter(([k]) => k.toLowerCase() !== "authorization")
      .map(([, v]) => v)
      .join("|");
    expect(allHeaderValuesExceptAuth.includes(FAKE_TOKEN)).toBe(false);
  });
});

describe("startRun: 4xx -> typed error", () => {
  it("throws StartRunError with code http_4xx and the status", async () => {
    const { fetchImpl } = makeFakeFetch({
      status: 400,
      body: { ok: false, errors: ["initial_input/website: not a URL"] },
    });
    await expect(
      startRun(
        { company_name: "Acme", website: "not-a-url" },
        { fetchImpl, env: ENV },
      ),
    ).rejects.toMatchObject({
      name: "StartRunError",
      code: "http_4xx",
      status: 400,
    });
  });

  it("treats 401 / 403 / 404 / 422 as http_4xx", async () => {
    for (const status of [401, 403, 404, 422]) {
      const { fetchImpl } = makeFakeFetch({ status, body: { ok: false } });
      await expect(
        startRun(
          { company_name: "Acme", website: "https://acme.example" },
          { fetchImpl, env: ENV },
        ),
      ).rejects.toMatchObject({ code: "http_4xx", status });
    }
  });

  it("does NOT include the bearer token in the thrown error message", async () => {
    const { fetchImpl } = makeFakeFetch({ status: 400, body: { ok: false } });
    let caught: unknown;
    try {
      await startRun(
        { company_name: "Acme", website: "https://acme.example" },
        { fetchImpl, env: ENV },
      );
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(StartRunError);
    const err = caught as StartRunError;
    expect(err.message.includes(FAKE_TOKEN)).toBe(false);
  });
});

describe("startRun: 5xx -> typed error", () => {
  it("throws StartRunError with code http_5xx and the status", async () => {
    const { fetchImpl } = makeFakeFetch({
      status: 503,
      body: "<html>upstream</html>",
    });
    await expect(
      startRun(
        { company_name: "Acme", website: "https://acme.example" },
        { fetchImpl, env: ENV },
      ),
    ).rejects.toMatchObject({ code: "http_5xx", status: 503 });
  });
});

describe("startRun: malformed 2xx -> bad_response", () => {
  it("throws bad_response when the body is not JSON", async () => {
    const { fetchImpl } = makeFakeFetch({ status: 200, body: "<html>" });
    await expect(
      startRun(
        { company_name: "Acme", website: "https://acme.example" },
        { fetchImpl, env: ENV },
      ),
    ).rejects.toMatchObject({ code: "bad_response" });
  });

  it("throws bad_response when run_id is missing or wrong type", async () => {
    for (const body of [{}, { run_id: 42 }, { runId: "x" }, null]) {
      const { fetchImpl } = makeFakeFetch({ status: 200, body });
      await expect(
        startRun(
          { company_name: "Acme", website: "https://acme.example" },
          { fetchImpl, env: ENV },
        ),
      ).rejects.toMatchObject({ code: "bad_response" });
    }
  });
});

describe("startRun: network error -> typed error", () => {
  it("throws StartRunError(code=network) on transport failure", async () => {
    const { fetchImpl } = makeFakeFetch({
      throwError: new Error("ECONNREFUSED"),
    });
    await expect(
      startRun(
        { company_name: "Acme", website: "https://acme.example" },
        { fetchImpl, env: ENV },
      ),
    ).rejects.toMatchObject({
      name: "StartRunError",
      code: "network",
    });
  });

  it("throws StartRunError(code=timeout) when the underlying fetch raises AbortError", async () => {
    // Simulate an AbortError (e.g., DNS-stage abort, signal-driven cancel).
    const abortErr = Object.assign(new Error("aborted"), { name: "AbortError" });
    const { fetchImpl } = makeFakeFetch({ throwError: abortErr });
    await expect(
      startRun(
        { company_name: "Acme", website: "https://acme.example" },
        { fetchImpl, env: ENV },
      ),
    ).rejects.toMatchObject({ name: "StartRunError", code: "timeout" });
  });

  it("throws StartRunError(code=timeout) when the request exceeds the timeout budget", async () => {
    const { fetchImpl } = makeFakeFetch({ delayMs: 200 });
    await expect(
      startRun(
        { company_name: "Acme", website: "https://acme.example" },
        { fetchImpl, env: ENV, timeoutMs: 50 },
      ),
    ).rejects.toMatchObject({ name: "StartRunError", code: "timeout" });
  });
});

describe("startRun: missing env -> config_missing", () => {
  it("throws config_missing when MURMUR_URL is unset", async () => {
    const { fetchImpl, calls } = makeFakeFetch({});
    await expect(
      startRun(
        { company_name: "Acme", website: "https://acme.example" },
        { fetchImpl, env: { MURMUR_URL: "", MURMUR_TOKEN: FAKE_TOKEN } },
      ),
    ).rejects.toMatchObject({ code: "config_missing" });
    expect(calls).toHaveLength(0);
  });

  it("throws config_missing when MURMUR_TOKEN is unset", async () => {
    const { fetchImpl, calls } = makeFakeFetch({});
    await expect(
      startRun(
        { company_name: "Acme", website: "https://acme.example" },
        { fetchImpl, env: { MURMUR_URL: FAKE_URL, MURMUR_TOKEN: undefined } },
      ),
    ).rejects.toMatchObject({ code: "config_missing" });
    expect(calls).toHaveLength(0);
  });

  it("error message references variable NAMES, never the missing/known token value", async () => {
    const { fetchImpl } = makeFakeFetch({});
    let caught: unknown;
    try {
      await startRun(
        { company_name: "Acme", website: "https://acme.example" },
        { fetchImpl, env: { MURMUR_URL: "", MURMUR_TOKEN: FAKE_TOKEN } },
      );
    } catch (e) {
      caught = e;
    }
    const err = caught as StartRunError;
    expect(err.message).toMatch(/MURMUR_URL/);
    expect(err.message.includes(FAKE_TOKEN)).toBe(false);
  });
});
