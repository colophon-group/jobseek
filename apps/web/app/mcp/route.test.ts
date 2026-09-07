/**
 * Tests for the `/mcp` route's instrumentation wrapper. The handler
 * itself lives in `@jseek/mcp-server/handler` (separate workspace
 * package); we mock it so this file exercises only the wrapper:
 * body inspection, structured log shape, error-path safety, and CORS.
 *
 * Regression context: #2647 (instrumentation must never break the
 * underlying request).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setTestEnv, withTestEnv } from "@/test-utils/env";

const mocks = vi.hoisted(() => ({
  afterPromises: [] as Promise<void>[],
  getClientIp: vi.fn(),
  handleMcpRequest: vi.fn(),
  recordPublicApiMetric: vi.fn(),
}));

vi.mock("next/server", () => ({
  after: vi.fn((callback: () => Promise<void> | void) => {
    mocks.afterPromises.push(Promise.resolve(callback()));
  }),
}));

vi.mock("@jseek/mcp-server/handler", () => ({
  handleMcpRequest: mocks.handleMcpRequest,
}));

vi.mock("@/lib/rate-limit", () => ({
  getClientIp: mocks.getClientIp,
}));

vi.mock("@/lib/public-api-metrics", () => ({
  recordPublicApiMetric: mocks.recordPublicApiMetric,
}));

import { DELETE, GET, OPTIONS, POST } from "./route";

let infoSpy: ReturnType<typeof vi.spyOn>;

withTestEnv({ HOSTED_MCP_API_PROVENANCE_TOKEN: undefined });

beforeEach(() => {
  mocks.afterPromises.length = 0;
  mocks.getClientIp.mockReset();
  mocks.getClientIp.mockReturnValue("203.0.113.10");
  mocks.handleMcpRequest.mockReset();
  mocks.recordPublicApiMetric.mockReset();
  mocks.recordPublicApiMetric.mockResolvedValue(undefined);
  infoSpy = vi.spyOn(console, "info").mockImplementation(() => {});
});

afterEach(async () => {
  await Promise.all(mocks.afterPromises);
  infoSpy.mockRestore();
});

const _logEntry = () => {
  // First log call: we always emit `console.info("[mcp]", { ... })`.
  expect(infoSpy).toHaveBeenCalledTimes(1);
  const args = infoSpy.mock.calls[0]!;
  expect(args[0]).toBe("[mcp]");
  return args[1] as Record<string, unknown>;
};

describe("/mcp instrumentation", () => {
  it("logs verb, rpc method, tool name, status, body_bytes for tools/call", async () => {
    mocks.handleMcpRequest.mockResolvedValueOnce(new Response("ok", { status: 200 }));
    const body = JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "search_jobs", arguments: { query: "secret" } },
    });

    const res = await POST(
      new Request("http://localhost/mcp", { method: "POST", body }),
    );

    expect(res.status).toBe(200);
    const entry = _logEntry();
    expect(entry.verb).toBe("POST");
    expect(entry.rpc_method).toBe("tools/call");
    expect(entry.tool).toBe("search_jobs");
    expect(entry.status).toBe(200);
    expect(entry.body_bytes).toBe(body.length);
    expect(entry.error).toBeNull();
    expect(typeof entry.handler_duration_ms).toBe("number");
  });

  it("does NOT log tool arguments (PII / freetext leak guard)", async () => {
    mocks.handleMcpRequest.mockResolvedValueOnce(new Response("ok", { status: 200 }));
    await POST(
      new Request("http://localhost/mcp", {
        method: "POST",
        body: JSON.stringify({
          method: "tools/call",
          params: { name: "search_jobs", arguments: { query: "TOPSECRET-QUERY" } },
        }),
      }),
    );

    const entry = _logEntry();
    // The arguments object must never appear in any log field.
    const serialized = JSON.stringify(entry);
    expect(serialized).not.toContain("TOPSECRET-QUERY");
    expect(serialized).not.toContain("arguments");
  });

  it("preserves the body for the inner handler (clone, not consume)", async () => {
    /** Regression: instrumentation reads `req.clone().text()` so the
     *  inner handler can still drain the body. If we ever switch to
     *  `req.text()` directly the handler would see an empty body. */
    let handlerSawBody: string | null = null;
    mocks.handleMcpRequest.mockImplementationOnce(async (req: Request) => {
      handlerSawBody = await req.text();
      return new Response("ok", { status: 200 });
    });

    const body = JSON.stringify({ method: "tools/list" });
    await POST(new Request("http://localhost/mcp", { method: "POST", body }));

    expect(handlerSawBody).toBe(body);
  });

  it("logs status=500 + a fixed error classification on handler throw, then rethrows", async () => {
    mocks.handleMcpRequest.mockRejectedValueOnce(
      new Error("SECRET_EXTERNAL_ERROR_CANARY"),
    );

    await expect(
      POST(new Request("http://localhost/mcp", { method: "POST", body: "{}" })),
    ).rejects.toThrow("SECRET_EXTERNAL_ERROR_CANARY");

    const entry = _logEntry();
    expect(entry.status).toBe(500);
    expect(entry.error).toBe("handler_error");
    expect(JSON.stringify(infoSpy.mock.calls)).not.toContain(
      "SECRET_EXTERNAL_ERROR_CANARY",
    );
  });

  it("never crashes the request when the body isn't JSON", async () => {
    mocks.handleMcpRequest.mockResolvedValueOnce(new Response("ok", { status: 200 }));

    const res = await POST(
      new Request("http://localhost/mcp", { method: "POST", body: "not-json" }),
    );

    expect(res.status).toBe(200);
    const entry = _logEntry();
    expect(entry.rpc_method).toBeNull();
    expect(entry.tool).toBeNull();
    expect(entry.body_bytes).toBe("not-json".length);
  });

  it("handles batch JSON-RPC by reporting the first call's method", async () => {
    mocks.handleMcpRequest.mockResolvedValueOnce(new Response("ok", { status: 200 }));

    const body = JSON.stringify([
      { method: "tools/call", params: { name: "first" } },
      { method: "tools/call", params: { name: "second" } },
    ]);
    await POST(new Request("http://localhost/mcp", { method: "POST", body }));

    const entry = _logEntry();
    expect(entry.rpc_method).toBe("tools/call");
    expect(entry.tool).toBe("unknown");
  });

  it.each([
    {
      caseName: "method",
      payload: { method: "SECRET_ARBITRARY_METHOD_CANARY" },
      expectedMethod: "unknown",
      expectedTool: null,
      canary: "SECRET_ARBITRARY_METHOD_CANARY",
    },
    {
      caseName: "tool name",
      payload: {
        method: "tools/call",
        params: { name: "SECRET_ARBITRARY_TOOL_CANARY" },
      },
      expectedMethod: "tools/call",
      expectedTool: "unknown",
      canary: "SECRET_ARBITRARY_TOOL_CANARY",
    },
  ])(
    "maps an attacker-controlled $caseName to a fixed enum",
    async ({ payload, expectedMethod, expectedTool, canary }) => {
      mocks.handleMcpRequest.mockResolvedValueOnce(
        new Response("ok", { status: 200 }),
      );

      await POST(
        new Request("http://localhost/mcp", {
          method: "POST",
          body: JSON.stringify(payload),
        }),
      );

      const entry = _logEntry();
      expect(entry.rpc_method).toBe(expectedMethod);
      expect(entry.tool).toBe(expectedTool);
      expect(JSON.stringify(infoSpy.mock.calls)).not.toContain(canary);
    },
  );

  it("preserves fixed supported method values", async () => {
    mocks.handleMcpRequest.mockResolvedValueOnce(
      new Response("ok", { status: 200 }),
    );
    await POST(
      new Request("http://localhost/mcp", {
        method: "POST",
        body: JSON.stringify({ method: "resources/templates/list" }),
      }),
    );

    expect(_logEntry().rpc_method).toBe("resources/templates/list");
  });

  it("classifies the retired search_watchlists tool as unknown", async () => {
    mocks.handleMcpRequest.mockResolvedValueOnce(
      new Response("ok", { status: 200 }),
    );
    await POST(
      new Request("http://localhost/mcp", {
        method: "POST",
        body: JSON.stringify({
          method: "tools/call",
          params: { name: "search_watchlists" },
        }),
      }),
    );

    expect(_logEntry().tool).toBe("unknown");
  });

  it("logs GET requests with body_bytes=0 and rpc_method=null", async () => {
    mocks.handleMcpRequest.mockResolvedValueOnce(new Response(null, { status: 405 }));
    const res = await GET(new Request("http://localhost/mcp"));
    expect(res.status).toBe(405);
    const entry = _logEntry();
    expect(entry.verb).toBe("GET");
    expect(entry.rpc_method).toBeNull();
    expect(entry.tool).toBeNull();
    expect(entry.body_bytes).toBe(0);
  });

  it("logs DELETE requests", async () => {
    mocks.handleMcpRequest.mockResolvedValueOnce(new Response(null, { status: 204 }));
    const res = await DELETE(
      new Request("http://localhost/mcp", { method: "DELETE" }),
    );
    expect(res.status).toBe(204);
    expect(_logEntry().verb).toBe("DELETE");
  });

  it("records OPTIONS exactly once without invoking the MCP handler", async () => {
    const request = new Request("http://localhost/mcp", {
      method: "OPTIONS",
      headers: { "x-forwarded-for": "203.0.113.10" },
    });

    const res = await OPTIONS(request);
    await Promise.all(mocks.afterPromises);

    expect(res.status).toBe(204);
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(mocks.handleMcpRequest).not.toHaveBeenCalled();
    expect(mocks.afterPromises).toHaveLength(1);
    expect(_logEntry()).toMatchObject({
      verb: "OPTIONS",
      rpc_method: null,
      tool: null,
      status: 204,
      body_bytes: 0,
      error: null,
    });
    expect(mocks.recordPublicApiMetric).toHaveBeenCalledTimes(1);
    expect(mocks.recordPublicApiMetric).toHaveBeenCalledWith({
      interface: "mcp",
      route: "mcp",
      consumer: "external",
      statusCode: 204,
      durationMs: expect.any(Number),
      rateLimited: false,
      clientIp: "203.0.113.10",
    });
  });

  it("attaches CORS headers to the inner response", async () => {
    mocks.handleMcpRequest.mockResolvedValueOnce(new Response("ok", { status: 200 }));
    const res = await POST(
      new Request("http://localhost/mcp", { method: "POST", body: "{}" }),
    );
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(res.headers.get("Access-Control-Expose-Headers")).toBe("Mcp-Session-Id");
  });

  it("non-string params.name does not produce a non-string tool field", async () => {
    mocks.handleMcpRequest.mockResolvedValueOnce(new Response("ok", { status: 200 }));
    await POST(
      new Request("http://localhost/mcp", {
        method: "POST",
        body: JSON.stringify({ method: "tools/call", params: { name: 42 } }),
      }),
    );
    const entry = _logEntry();
    expect(entry.rpc_method).toBe("tools/call");
    expect(entry.tool).toBeNull();
  });

  it("passes the private provenance token only when the hosted route is configured", async () => {
    const configuredRequest = new Request("http://localhost/mcp", {
      method: "POST",
      body: "{}",
    });
    setTestEnv({
      HOSTED_MCP_API_PROVENANCE_TOKEN: "private-hosted-token",
    });
    mocks.handleMcpRequest.mockResolvedValueOnce(
      new Response("ok", { status: 200 }),
    );

    await POST(configuredRequest);

    expect(mocks.handleMcpRequest).toHaveBeenLastCalledWith(
      configuredRequest,
      undefined,
      { internalMcpToken: "private-hosted-token" },
    );

    setTestEnv({ HOSTED_MCP_API_PROVENANCE_TOKEN: undefined });
    mocks.handleMcpRequest.mockResolvedValueOnce(
      new Response("ok", { status: 200 }),
    );
    const defaultRequest = new Request("http://localhost/mcp");
    await GET(defaultRequest);

    expect(mocks.handleMcpRequest).toHaveBeenLastCalledWith(defaultRequest);
  });

  it("records one incoming MCP aggregate without exposing the client IP in logs", async () => {
    mocks.handleMcpRequest.mockResolvedValueOnce(
      new Response("ok", { status: 429 }),
    );

    await GET(
      new Request("http://localhost/mcp?query=SECRET_QUERY_CANARY", {
        headers: { "x-forwarded-for": "203.0.113.10" },
      }),
    );
    await Promise.all(mocks.afterPromises);

    expect(mocks.recordPublicApiMetric).toHaveBeenCalledTimes(1);
    expect(mocks.recordPublicApiMetric).toHaveBeenCalledWith({
      interface: "mcp",
      route: "mcp",
      consumer: "external",
      statusCode: 429,
      durationMs: expect.any(Number),
      rateLimited: true,
      clientIp: "203.0.113.10",
    });
    const logs = JSON.stringify(infoSpy.mock.calls);
    expect(logs).not.toContain("203.0.113.10");
    expect(logs).not.toContain("SECRET_QUERY_CANARY");
  });

  it("isolates MCP telemetry failures from the response", async () => {
    infoSpy.mockImplementation(() => {
      throw new Error("logging unavailable");
    });
    mocks.recordPublicApiMetric.mockRejectedValueOnce(
      new Error("metrics unavailable"),
    );
    mocks.handleMcpRequest.mockResolvedValueOnce(
      new Response("preserved", { status: 200 }),
    );

    const response = await GET(new Request("http://localhost/mcp"));
    await expect(Promise.all(mocks.afterPromises)).resolves.toBeDefined();

    expect(await response.text()).toBe("preserved");
  });
});
