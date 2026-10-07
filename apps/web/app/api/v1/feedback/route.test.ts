// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { withTestEnv } from "@/test-utils/env";
import { MAX_FEEDBACK_BYTES } from "@jseek/mcp-server/feedback-contract";
import { MCP_SERVER_VERSION } from "@jseek/mcp-server/metadata";

const mocks = vi.hoisted(() => ({ values: vi.fn(), insert: vi.fn(), limit: vi.fn(), after: vi.fn() }));
vi.mock("@/db", () => ({ db: { insert: mocks.insert } }));
vi.mock("@/lib/rate-limit", () => ({ feedbackLimiter: { limit: mocks.limit }, getClientIp: () => "203.0.113.1" }));
vi.mock("@/lib/public-api-metrics", () => ({ recordPublicApiMetric: vi.fn() }));
vi.mock("next/server", async (importOriginal) => ({ ...await importOriginal<typeof import("next/server")>(), after: mocks.after }));
import { POST, OPTIONS } from "./route";

const bug = { kind: "bug", goal: "Find roles", affectedTool: "search_jobs", expected: "Return matches", observed: "An error occurred", impact: "blocked" };
const feature = { kind: "feature", goal: "Compare salaries", capability: "Salary in summaries", benefit: "Fewer detail calls", impact: "partial" };
const feedback = { kind: "feedback", goal: "Find roles", observation: "Useful company links" };
function request(input: unknown, headers: HeadersInit = {}) {
  return new NextRequest("https://example.test/api/v1/feedback", { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(input) });
}

describe("product feedback submission", () => {
  withTestEnv({ HOSTED_MCP_API_PROVENANCE_TOKEN: "private-test-provenance" });
  beforeEach(() => {
    mocks.values.mockReset().mockResolvedValue(undefined);
    mocks.insert.mockReset().mockReturnValue({ values: mocks.values });
    mocks.limit.mockReset().mockResolvedValue({ success: true, reset: Date.now() + 1000 });
    mocks.after.mockReset();
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => vi.restoreAllMocks());

  it.each([bug, feature, feedback])("stores a $kind submission with only basic acknowledgement", async (input) => {
    const response = await POST(request(input));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ success: true });
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    const { kind, ...payload } = input;
    expect(mocks.values).toHaveBeenCalledWith({ kind, payload, serverVersion: MCP_SERVER_VERSION, consumer: "external" });
  });

  it("appends repeated submissions without a deduplication contract", async () => {
    await POST(request(feedback)); await POST(request(feedback));
    expect(mocks.values).toHaveBeenCalledTimes(2);
  });

  it.each([{}, { ...bug, expected: " " }, { ...feature, impact: "critical" }, { ...feedback, transcript: "PRIVATE_CANARY" }, { ...bug, affectedTool: "trigger_ghost_analysis" }, { ...feedback, observation: "x".repeat(1001) }])("rejects malformed or undeclared fields before storage and budget consumption", async (input) => {
    const response = await POST(request(input));
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ success: false, error: "invalid_submission" });
    expect(mocks.insert).not.toHaveBeenCalled(); expect(mocks.limit).not.toHaveBeenCalled();
  });

  it("bounds actual bytes even with a false Content-Length", async () => {
    const response = await POST(new NextRequest("https://example.test/api/v1/feedback", { method: "POST", headers: { "Content-Type": "application/json", "Content-Length": "1" }, body: "€".repeat(MAX_FEEDBACK_BYTES) }));
    expect(response.status).toBe(413); expect(mocks.insert).not.toHaveBeenCalled();
  });
  it("rejects non-JSON and malformed JSON", async () => {
    expect((await POST(request(feedback, { "Content-Type": "text/plain" }))).status).toBe(415);
    const response = await POST(new NextRequest("https://example.test/api/v1/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{" }));
    expect(response.status).toBe(400);
  });
  it("enforces the feedback budget and fails closed if the limiter is unavailable", async () => {
    mocks.limit.mockResolvedValueOnce({ success: false, reset: Date.now() + 60000 });
    const response = await POST(request(feedback));
    expect(response.status).toBe(429); expect(Number(response.headers.get("retry-after"))).toBeGreaterThan(0);
    mocks.limit.mockRejectedValueOnce(new Error("PRIVATE_CANARY"));
    expect((await POST(request(feedback))).status).toBe(503);
    expect(mocks.insert).not.toHaveBeenCalled();
    expect(JSON.stringify(vi.mocked(console.warn).mock.calls)).not.toContain("PRIVATE_CANARY");
  });
  it("does not acknowledge an unsuccessful storage write or expose its error", async () => {
    mocks.values.mockRejectedValueOnce(new Error("PRIVATE_CANARY"));
    const response = await POST(request(feedback));
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ success: false, error: "temporarily_unavailable" });
    expect(JSON.stringify(vi.mocked(console.error).mock.calls)).not.toContain("PRIVATE_CANARY");
  });
  it.each([undefined, "forged", "private-test-provenance"])("trusts a forwarded client IP only with protected provenance (%s)", async (token) => {
    const headers: Record<string, string> = { "x-jobseek-mcp-client-ip": "203.0.113.42" };
    if (token) headers["x-jobseek-internal-mcp-token"] = token;
    await POST(request(feedback, headers));
    const trusted = token === "private-test-provenance";
    expect(mocks.limit).toHaveBeenCalledWith(trusted ? "203.0.113.42" : "203.0.113.1");
    const stored = mocks.values.mock.calls[0][0];
    expect(stored.consumer).toBe(trusted ? "hosted_mcp" : "external");
    expect(JSON.stringify(stored)).not.toMatch(/203\.0\.113|private-test-provenance/);
  });
  it("supports JSON POST preflight", () => {
    const response = OPTIONS();
    expect(response.status).toBe(204);
    expect(response.headers.get("access-control-allow-methods")).toBe("POST, OPTIONS");
  });
});
