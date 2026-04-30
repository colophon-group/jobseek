import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `server-only` is a build-time guard the route imports transitively via
// `@/lib/sessionCache`. The admin-route tests use the same shim.
vi.mock("server-only", () => ({}));

// Mock the session cache so we can flip the user id between tests without
// needing a real better-auth session.
const mockGetSessionUserId = vi.fn<() => Promise<string | null>>();
vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: () => mockGetSessionUserId(),
}));

// Mock startRun while preserving the real `StartRunError` class shape so the
// `instanceof` check inside the route works.
vi.mock("@/lib/murmur/start-run", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/murmur/start-run")
  >("@/lib/murmur/start-run");
  return {
    StartRunError: actual.StartRunError,
    startRun: vi.fn(),
  };
});

import { startRun, StartRunError } from "@/lib/murmur/start-run";
import {
  POST,
  __resetRateLimitForTests,
  buildAgentPrompt,
  consumeRateLimit,
  parseBody,
} from "./route";

const URL_BASE = "http://localhost/api/web/companies/request";
const USER_ID = "user_demo_001";

function makeRequest(body: unknown, init?: RequestInit): Request {
  return new Request(URL_BASE, {
    method: "POST",
    body: typeof body === "string" ? body : JSON.stringify(body),
    headers: { "content-type": "application/json" },
    ...init,
  });
}

describe("POST /api/web/companies/request", () => {
  beforeEach(() => {
    process.env.MURMUR_RUN_TRIGGER_ENABLED = "true";
    vi.mocked(startRun).mockReset();
    mockGetSessionUserId.mockReset();
    mockGetSessionUserId.mockResolvedValue(USER_ID);
    __resetRateLimitForTests();
  });

  afterEach(() => {
    delete process.env.MURMUR_RUN_TRIGGER_ENABLED;
  });

  it("rejects unauthenticated calls with 401", async () => {
    mockGetSessionUserId.mockResolvedValue(null);
    const res = await POST(
      makeRequest({ company_name: "Stripe", website: "https://stripe.com" }),
    );
    expect(res.status).toBe(401);
    await expect(res.json()).resolves.toEqual({
      ok: false,
      errors: ["unauthorized"],
    });
    expect(vi.mocked(startRun)).not.toHaveBeenCalled();
  });

  it("returns 503 when the feature flag is unset", async () => {
    delete process.env.MURMUR_RUN_TRIGGER_ENABLED;
    const res = await POST(
      makeRequest({ company_name: "Stripe", website: "https://stripe.com" }),
    );
    expect(res.status).toBe(503);
    await expect(res.json()).resolves.toEqual({
      ok: false,
      errors: ["disabled"],
    });
    expect(vi.mocked(startRun)).not.toHaveBeenCalled();
  });

  it("returns 503 when the feature flag is set to a non-true value", async () => {
    process.env.MURMUR_RUN_TRIGGER_ENABLED = "false";
    const res = await POST(
      makeRequest({ company_name: "Stripe", website: "https://stripe.com" }),
    );
    expect(res.status).toBe(503);
    expect(vi.mocked(startRun)).not.toHaveBeenCalled();
  });

  it("returns 400 with validation:body:json when body is not JSON", async () => {
    const res = await POST(makeRequest("not json"));
    expect(res.status).toBe(400);
    const body = (await res.json()) as { ok: boolean; errors: string[] };
    expect(body.ok).toBe(false);
    expect(body.errors).toContain("validation:body:json");
    expect(vi.mocked(startRun)).not.toHaveBeenCalled();
  });

  it("returns 400 with field-path errors when company_name is empty", async () => {
    const res = await POST(
      makeRequest({ company_name: "  ", website: "https://stripe.com" }),
    );
    expect(res.status).toBe(400);
    const body = (await res.json()) as { ok: boolean; errors: string[] };
    expect(body.ok).toBe(false);
    expect(body.errors.some((e) => e.startsWith("validation:company_name:"))).toBe(
      true,
    );
    expect(vi.mocked(startRun)).not.toHaveBeenCalled();
  });

  it("returns 400 with field-path errors when company_name is missing", async () => {
    const res = await POST(makeRequest({ website: "https://stripe.com" }));
    expect(res.status).toBe(400);
    const body = (await res.json()) as { ok: boolean; errors: string[] };
    expect(body.errors.some((e) => e.startsWith("validation:company_name:"))).toBe(
      true,
    );
  });

  it("returns 400 when website is not a valid URL", async () => {
    const res = await POST(
      makeRequest({ company_name: "Stripe", website: "not a url" }),
    );
    expect(res.status).toBe(400);
    const body = (await res.json()) as { ok: boolean; errors: string[] };
    expect(body.errors.some((e) => e.startsWith("validation:website:"))).toBe(true);
    expect(vi.mocked(startRun)).not.toHaveBeenCalled();
  });

  it("returns 400 when website uses a non-http(s) protocol", async () => {
    const res = await POST(
      makeRequest({ company_name: "Stripe", website: "ftp://stripe.com" }),
    );
    expect(res.status).toBe(400);
    const body = (await res.json()) as { ok: boolean; errors: string[] };
    expect(body.errors.some((e) => e.startsWith("validation:website:"))).toBe(true);
  });

  it("returns 400 when both fields are wrong types", async () => {
    const res = await POST(makeRequest({ company_name: 42, website: 99 }));
    expect(res.status).toBe(400);
    const body = (await res.json()) as { ok: boolean; errors: string[] };
    expect(body.ok).toBe(false);
    expect(body.errors.length).toBeGreaterThanOrEqual(1);
  });

  it("happy path: returns run_id and agent_prompt with company name + run id", async () => {
    vi.mocked(startRun).mockResolvedValue({
      run_id: "r_abc123",
    });
    const res = await POST(
      makeRequest({ company_name: "Stripe", website: "https://stripe.com" }),
    );
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      ok: true;
      data: { run_id: string; agent_prompt: string };
    };
    expect(body.ok).toBe(true);
    expect(body.data.run_id).toBe("r_abc123");
    expect(body.data.agent_prompt).toContain("Stripe");
    expect(body.data.agent_prompt).toContain("https://stripe.com");
    expect(body.data.agent_prompt).toContain("r_abc123");
    expect(vi.mocked(startRun)).toHaveBeenCalledWith({
      company_name: "Stripe",
      website: "https://stripe.com",
    });
  });

  it("trims whitespace on company_name and website before forwarding", async () => {
    vi.mocked(startRun).mockResolvedValue({ run_id: "r_x" });
    const res = await POST(
      makeRequest({
        company_name: "  Stripe  ",
        website: "  https://stripe.com  ",
      }),
    );
    expect(res.status).toBe(200);
    expect(vi.mocked(startRun)).toHaveBeenCalledWith({
      company_name: "Stripe",
      website: "https://stripe.com",
    });
  });

  it("maps StartRunError(http_5xx) to 502", async () => {
    vi.mocked(startRun).mockRejectedValue(
      new StartRunError("http_5xx", "Murmur returned HTTP 502", { status: 502 }),
    );
    const res = await POST(
      makeRequest({ company_name: "Stripe", website: "https://stripe.com" }),
    );
    expect(res.status).toBe(502);
    await expect(res.json()).resolves.toEqual({
      ok: false,
      errors: ["upstream:http_5xx"],
    });
  });

  it("maps StartRunError(timeout) to 504", async () => {
    vi.mocked(startRun).mockRejectedValue(
      new StartRunError("timeout", "request timed out"),
    );
    const res = await POST(
      makeRequest({ company_name: "Stripe", website: "https://stripe.com" }),
    );
    expect(res.status).toBe(504);
    await expect(res.json()).resolves.toEqual({
      ok: false,
      errors: ["upstream:timeout"],
    });
  });

  it("maps StartRunError(network) to 502", async () => {
    vi.mocked(startRun).mockRejectedValue(
      new StartRunError("network", "DNS failed"),
    );
    const res = await POST(
      makeRequest({ company_name: "Stripe", website: "https://stripe.com" }),
    );
    expect(res.status).toBe(502);
    await expect(res.json()).resolves.toEqual({
      ok: false,
      errors: ["upstream:network"],
    });
  });

  it("maps StartRunError(config_missing) to 503", async () => {
    vi.mocked(startRun).mockRejectedValue(
      new StartRunError("config_missing", "missing MURMUR_URL"),
    );
    const res = await POST(
      makeRequest({ company_name: "Stripe", website: "https://stripe.com" }),
    );
    expect(res.status).toBe(503);
    await expect(res.json()).resolves.toEqual({
      ok: false,
      errors: ["upstream:config_missing"],
    });
  });

  it("returns 429 after the 5th request from the same user within the window", async () => {
    vi.mocked(startRun).mockResolvedValue({ run_id: "r_ok" });
    for (let i = 0; i < 5; i++) {
      const res = await POST(
        makeRequest({
          company_name: `Co${i}`,
          website: "https://example.com",
        }),
      );
      expect(res.status).toBe(200);
    }
    // 6th call must be rate-limited.
    const blocked = await POST(
      makeRequest({ company_name: "Co5", website: "https://example.com" }),
    );
    expect(blocked.status).toBe(429);
    await expect(blocked.json()).resolves.toEqual({
      ok: false,
      errors: ["rate_limited"],
    });
    // startRun must NOT have been invoked the 6th time.
    expect(vi.mocked(startRun)).toHaveBeenCalledTimes(5);
  });

  it("rate-limit is per-user (different user id is not affected)", async () => {
    vi.mocked(startRun).mockResolvedValue({ run_id: "r_ok" });
    for (let i = 0; i < 5; i++) {
      const res = await POST(
        makeRequest({ company_name: `Co${i}`, website: "https://example.com" }),
      );
      expect(res.status).toBe(200);
    }
    // Switch user — first request from the new user must succeed.
    mockGetSessionUserId.mockResolvedValue("other_user");
    const res = await POST(
      makeRequest({ company_name: "Other", website: "https://example.com" }),
    );
    expect(res.status).toBe(200);
  });

  it("does not echo the website value or env var values in error envelopes", async () => {
    process.env.MURMUR_TOKEN = "super_secret_token_42";
    vi.mocked(startRun).mockRejectedValue(
      new StartRunError("network", "DNS failed for https://stripe.com"),
    );
    const res = await POST(
      makeRequest({
        company_name: "Stripe",
        website: "https://stripe.com",
      }),
    );
    const body = (await res.json()) as { errors: string[] };
    const joined = body.errors.join(" ");
    expect(joined).not.toContain("https://stripe.com");
    expect(joined).not.toContain("super_secret_token_42");
    delete process.env.MURMUR_TOKEN;
  });

  it("returns 401 (not 500) when the session lookup itself throws", async () => {
    mockGetSessionUserId.mockRejectedValue(new Error("redis exploded"));
    const res = await POST(
      makeRequest({ company_name: "Stripe", website: "https://stripe.com" }),
    );
    expect(res.status).toBe(401);
  });

  it("validates body BEFORE consuming rate-limit credits", async () => {
    // 5 malformed requests must not exhaust the user's 5/h budget.
    for (let i = 0; i < 5; i++) {
      const res = await POST(makeRequest({ company_name: "", website: "" }));
      expect(res.status).toBe(400);
    }
    vi.mocked(startRun).mockResolvedValue({ run_id: "r_ok" });
    const ok = await POST(
      makeRequest({ company_name: "Stripe", website: "https://stripe.com" }),
    );
    expect(ok.status).toBe(200);
  });
});

describe("parseBody", () => {
  it("rejects null / arrays / non-objects", () => {
    expect(parseBody(null).ok).toBe(false);
    expect(parseBody([]).ok).toBe(false);
    expect(parseBody("hello").ok).toBe(false);
    expect(parseBody(42).ok).toBe(false);
  });

  it("trims and accepts a valid body", () => {
    const r = parseBody({
      company_name: "  Stripe ",
      website: " https://stripe.com ",
    });
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.value).toEqual({
        company_name: "Stripe",
        website: "https://stripe.com",
      });
    }
  });

  it("flags non-string fields with type errors", () => {
    const r = parseBody({ company_name: 1, website: true });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.errors).toEqual(
        expect.arrayContaining([
          "validation:company_name:type",
          "validation:website:type",
        ]),
      );
    }
  });

  it("rejects javascript: URLs (non-http(s) protocol)", () => {
    const r = parseBody({
      company_name: "Stripe",
      website: "javascript:alert(1)",
    });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.errors.some((e) => e.startsWith("validation:website:"))).toBe(true);
    }
  });
});

describe("consumeRateLimit", () => {
  beforeEach(() => {
    __resetRateLimitForTests();
  });

  it("allows up to 5 requests within the window", () => {
    for (let i = 0; i < 5; i++) {
      expect(consumeRateLimit("u", 1_000_000).ok).toBe(true);
    }
    expect(consumeRateLimit("u", 1_000_000).ok).toBe(false);
  });

  it("resets after the 60-min window elapses", () => {
    const start = 1_000_000;
    for (let i = 0; i < 5; i++) {
      expect(consumeRateLimit("u", start).ok).toBe(true);
    }
    // Just before the window expires — still blocked.
    expect(consumeRateLimit("u", start + 60 * 60 * 1000 - 1).ok).toBe(false);
    // After the window — allowed again.
    expect(consumeRateLimit("u", start + 60 * 60 * 1000 + 1).ok).toBe(true);
  });

  it("partitions the budget per user", () => {
    for (let i = 0; i < 5; i++) {
      consumeRateLimit("a", 1_000_000);
    }
    expect(consumeRateLimit("a", 1_000_000).ok).toBe(false);
    expect(consumeRateLimit("b", 1_000_000).ok).toBe(true);
  });
});

describe("buildAgentPrompt", () => {
  it("includes company name, website, and run_id verbatim", () => {
    const out = buildAgentPrompt({
      company_name: "Stripe",
      website: "https://stripe.com",
      run_id: "r_abc123",
    });
    expect(out).toContain("Stripe");
    expect(out).toContain("https://stripe.com");
    expect(out).toContain("r_abc123");
    // The MCP tool name is part of the prompt the issue spec calls out.
    expect(out).toContain("pull_task");
  });
});
