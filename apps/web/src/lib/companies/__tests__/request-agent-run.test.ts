import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { requestAgentRun } from "../request-agent-run";

const ENDPOINT = "/api/web/companies/request";

function mockFetch(impl: typeof fetch) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("requestAgentRun", () => {
  it("returns ok with run_id and agent_prompt on 200", async () => {
    mockFetch(async () =>
      jsonResponse(200, {
        ok: true,
        data: { run_id: "run_abc", agent_prompt: "do the thing" },
      }),
    );

    const result = await requestAgentRun({
      companyName: "Acme",
      website: "https://acme.example",
    });

    expect(result).toEqual({
      kind: "ok",
      runId: "run_abc",
      agentPrompt: "do the thing",
    });
  });

  it("posts JSON body to the documented endpoint", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { ok: true, data: { run_id: "r", agent_prompt: "p" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await requestAgentRun({
      companyName: "Acme",
      website: "https://acme.example",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const call = fetchMock.mock.calls[0];
    if (!call) throw new Error("fetch was not called");
    const [url, init] = call as unknown as [string, RequestInit];
    expect(url).toBe(ENDPOINT);
    expect(init.method).toBe("POST");
    expect(
      (init.headers as Record<string, string>)["content-type"] ??
        (init.headers as Record<string, string>)["Content-Type"],
    ).toMatch(/application\/json/i);
    expect(JSON.parse(init.body as string)).toEqual({
      company_name: "Acme",
      website: "https://acme.example",
    });
  });

  it("returns disabled on 503 with errors:['disabled']", async () => {
    mockFetch(async () =>
      jsonResponse(503, { ok: false, errors: ["disabled"] }),
    );

    const result = await requestAgentRun({
      companyName: "X",
      website: "https://x.example",
    });

    expect(result).toEqual({ kind: "disabled" });
  });

  it("returns rate_limited on 429", async () => {
    mockFetch(async () =>
      jsonResponse(429, { ok: false, errors: ["rate_limited"] }),
    );

    const result = await requestAgentRun({
      companyName: "X",
      website: "https://x.example",
    });

    expect(result).toEqual({ kind: "rate_limited" });
  });

  it("returns unauthorized on 401", async () => {
    mockFetch(async () =>
      jsonResponse(401, { ok: false, errors: ["unauthorized"] }),
    );

    const result = await requestAgentRun({
      companyName: "X",
      website: "https://x.example",
    });

    expect(result).toEqual({ kind: "unauthorized" });
  });

  it("returns validation with the codes echoed by the server on 400", async () => {
    mockFetch(async () =>
      jsonResponse(400, {
        ok: false,
        errors: ["validation:website:url"],
      }),
    );

    const result = await requestAgentRun({
      companyName: "X",
      website: "not-a-url",
    });

    expect(result).toEqual({
      kind: "validation",
      codes: ["validation:website:url"],
    });
  });

  it("returns error on other 5xx (e.g. 502 upstream)", async () => {
    mockFetch(async () =>
      jsonResponse(502, { ok: false, errors: ["upstream:http_5xx"] }),
    );

    const result = await requestAgentRun({
      companyName: "X",
      website: "https://x.example",
    });

    expect(result).toEqual({ kind: "error" });
  });

  it("returns error when fetch throws (network failure)", async () => {
    mockFetch(async () => {
      throw new TypeError("network down");
    });

    const result = await requestAgentRun({
      companyName: "X",
      website: "https://x.example",
    });

    expect(result).toEqual({ kind: "error" });
  });

  it("returns error when body is not parseable JSON", async () => {
    mockFetch(async () =>
      new Response("<html>oops</html>", {
        status: 500,
        headers: { "content-type": "text/html" },
      }),
    );

    const result = await requestAgentRun({
      companyName: "X",
      website: "https://x.example",
    });

    expect(result).toEqual({ kind: "error" });
  });

  it("returns error when 200 envelope is missing data fields", async () => {
    mockFetch(async () => jsonResponse(200, { ok: true, data: {} }));

    const result = await requestAgentRun({
      companyName: "X",
      website: "https://x.example",
    });

    expect(result).toEqual({ kind: "error" });
  });
});
