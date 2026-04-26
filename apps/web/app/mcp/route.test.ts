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

const mocks = vi.hoisted(() => ({
  handleMcpRequest: vi.fn(),
}));

vi.mock("@jseek/mcp-server/handler", () => ({
  handleMcpRequest: mocks.handleMcpRequest,
}));

import { DELETE, GET, OPTIONS, POST } from "./route";

let infoSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  mocks.handleMcpRequest.mockReset();
  infoSpy = vi.spyOn(console, "info").mockImplementation(() => {});
});

afterEach(() => {
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

  it("logs status=500 + error message on handler throw, then rethrows", async () => {
    mocks.handleMcpRequest.mockRejectedValueOnce(new Error("boom"));

    await expect(
      POST(new Request("http://localhost/mcp", { method: "POST", body: "{}" })),
    ).rejects.toThrow("boom");

    const entry = _logEntry();
    expect(entry.status).toBe(500);
    expect(entry.error).toBe("boom");
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
    expect(entry.tool).toBe("first");
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

  it("OPTIONS does not invoke the handler and emits no log", async () => {
    /** Preflight is short-circuited above the instrument() wrapper —
     *  it returns the canned 204 directly so we don't pay for handler
     *  setup, and we don't pollute logs with one entry per CORS probe. */
    const res = await OPTIONS();
    expect(res.status).toBe(204);
    expect(mocks.handleMcpRequest).not.toHaveBeenCalled();
    expect(infoSpy).not.toHaveBeenCalled();
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
});
