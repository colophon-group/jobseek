import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildRunStatusUrl,
  fetchMurmurRunStatus,
  RunStatusError,
  type FetchImpl,
} from "./run-status";

const ENV_OK = {
  MURMUR_URL: "https://murmur.example.com",
  MURMUR_TOKEN: "tok-secret-do-not-log",
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("buildRunStatusUrl", () => {
  it("composes base + /runs/{run_id} with trailing slash tolerance", () => {
    expect(buildRunStatusUrl("https://m.example", "r_abc")).toBe(
      "https://m.example/runs/r_abc",
    );
    expect(buildRunStatusUrl("https://m.example/", "r_abc")).toBe(
      "https://m.example/runs/r_abc",
    );
    expect(buildRunStatusUrl("https://m.example//", "r_abc")).toBe(
      "https://m.example/runs/r_abc",
    );
  });

  it("URL-encodes the run id so unusual ids round-trip safely", () => {
    expect(buildRunStatusUrl("https://m.example", "weird id/?")).toBe(
      "https://m.example/runs/weird%20id%2F%3F",
    );
  });
});

describe("fetchMurmurRunStatus", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the {status, webhook_status} subset, dropping agent_actions", async () => {
    const captured: { url: string | URL; init?: RequestInit }[] = [];
    const fetchImpl: FetchImpl = vi.fn(async (url, init) => {
      captured.push({ url, init });
      return jsonResponse(200, {
        ok: true,
        data: {
          run_id: "r_abc",
          pipeline_id: "jobseek-add-company",
          status: "completed",
          webhook_status: "delivered",
          agent_actions: [
            { kind: "subagent.run", payload: { secret: "do-not-leak" } },
          ],
        },
      });
    });

    const out = await fetchMurmurRunStatus("r_abc", {
      env: ENV_OK,
      fetchImpl,
    });

    expect(out).toEqual({ status: "completed", webhook_status: "delivered" });
    expect(JSON.stringify(out)).not.toContain("agent_actions");
    expect(JSON.stringify(out)).not.toContain("do-not-leak");
    // The URL must be the run-status URL and the auth header must be set;
    // body must be empty (it's a GET).
    expect(captured).toHaveLength(1);
    expect(String(captured[0].url)).toBe(
      "https://murmur.example.com/runs/r_abc",
    );
    expect(captured[0].init?.method).toBe("GET");
    expect(
      (captured[0].init?.headers as Record<string, string>).Authorization,
    ).toBe("Bearer tok-secret-do-not-log");
  });

  it("throws config_missing when MURMUR_URL is unset", async () => {
    await expect(
      fetchMurmurRunStatus("r_abc", {
        env: { MURMUR_URL: "", MURMUR_TOKEN: "tok" },
        fetchImpl: vi.fn(),
      }),
    ).rejects.toMatchObject({ name: "RunStatusError", code: "config_missing" });
  });

  it("throws config_missing when MURMUR_TOKEN is unset", async () => {
    await expect(
      fetchMurmurRunStatus("r_abc", {
        env: { MURMUR_URL: "https://m.example", MURMUR_TOKEN: "" },
        fetchImpl: vi.fn(),
      }),
    ).rejects.toMatchObject({ name: "RunStatusError", code: "config_missing" });
  });

  it("never includes the token value in the config_missing error message", async () => {
    try {
      await fetchMurmurRunStatus("r_abc", {
        env: { MURMUR_URL: "", MURMUR_TOKEN: "tok-secret-do-not-log" },
        fetchImpl: vi.fn(),
      });
      throw new Error("expected throw");
    } catch (err) {
      expect(err).toBeInstanceOf(RunStatusError);
      expect((err as Error).message).not.toContain("tok-secret-do-not-log");
    }
  });

  it("maps 4xx to http_4xx", async () => {
    const fetchImpl: FetchImpl = vi.fn(async () =>
      jsonResponse(404, { ok: false, errors: ["not_found"] }),
    );
    await expect(
      fetchMurmurRunStatus("r_missing", { env: ENV_OK, fetchImpl }),
    ).rejects.toMatchObject({ code: "http_4xx", status: 404 });
  });

  it("maps 5xx to http_5xx", async () => {
    const fetchImpl: FetchImpl = vi.fn(async () =>
      jsonResponse(503, { ok: false }),
    );
    await expect(
      fetchMurmurRunStatus("r_abc", { env: ENV_OK, fetchImpl }),
    ).rejects.toMatchObject({ code: "http_5xx", status: 503 });
  });

  it("maps a non-JSON body to bad_response", async () => {
    const fetchImpl: FetchImpl = vi.fn(
      async () =>
        new Response("not json", {
          status: 200,
          headers: { "content-type": "text/plain" },
        }),
    );
    await expect(
      fetchMurmurRunStatus("r_abc", { env: ENV_OK, fetchImpl }),
    ).rejects.toMatchObject({ code: "bad_response" });
  });

  it("maps missing data.status / webhook_status to bad_response", async () => {
    const fetchImpl: FetchImpl = vi.fn(async () =>
      jsonResponse(200, { ok: true, data: { status: "completed" } }),
    );
    await expect(
      fetchMurmurRunStatus("r_abc", { env: ENV_OK, fetchImpl }),
    ).rejects.toMatchObject({ code: "bad_response" });
  });

  it("maps a generic fetch reject to network", async () => {
    const fetchImpl: FetchImpl = vi.fn(async () => {
      throw new Error("ECONNRESET");
    });
    await expect(
      fetchMurmurRunStatus("r_abc", { env: ENV_OK, fetchImpl }),
    ).rejects.toMatchObject({ code: "network" });
  });

  it("maps an AbortError to timeout", async () => {
    const fetchImpl: FetchImpl = vi.fn(async () => {
      const e = new Error("aborted");
      e.name = "AbortError";
      throw e;
    });
    await expect(
      fetchMurmurRunStatus("r_abc", { env: ENV_OK, fetchImpl, timeoutMs: 50 }),
    ).rejects.toMatchObject({ code: "timeout" });
  });
});
