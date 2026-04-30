import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mockGetSessionUserId = vi.fn<() => Promise<string | null>>();
vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: () => mockGetSessionUserId(),
}));

const mockFetchMurmurRunStatus =
  vi.fn<
    (
      runId: string,
    ) => Promise<{ status: string; webhook_status: string }>
  >();
vi.mock("@/lib/murmur/run-status", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/murmur/run-status")
  >("@/lib/murmur/run-status");
  return {
    ...actual,
    fetchMurmurRunStatus: (runId: string) => mockFetchMurmurRunStatus(runId),
  };
});

// `revalidatePath` and `revalidateTag` come from Next; mock them to assert the
// proxy fires both on the first delivered transition. We don't care about
// their real behaviour — just that they were called.
const mockRevalidatePath = vi.fn();
const mockRevalidateTag = vi.fn();
vi.mock("next/cache", () => ({
  revalidatePath: (...args: unknown[]) => mockRevalidatePath(...args),
  revalidateTag: (...args: unknown[]) => mockRevalidateTag(...args),
}));

// Stub the Drizzle query the route uses to look up the accept-log row.
// `lookupAcceptedCompany` is exported from the route so we can swap it
// independently of mocking the entire DB layer.
const mockLookup =
  vi.fn<
    (
      runId: string,
    ) => Promise<{ slug: string | null; companyId: string | null } | null>
  >();
vi.mock("./route", async () => {
  const actual = await vi.importActual<typeof import("./route")>("./route");
  return {
    ...actual,
    lookupAcceptedCompany: (runId: string) => mockLookup(runId),
  };
});

import { GET } from "./route";

const USER_ID = "user_demo_001";

function makeContext(runId: string) {
  return {
    params: Promise.resolve({ run_id: runId }),
  };
}

function makeRequest(): Request {
  return new Request("http://localhost/api/web/companies/request/r_x/status", {
    method: "GET",
  });
}

describe("GET /api/web/companies/request/[run_id]/status", () => {
  beforeEach(() => {
    process.env.MURMUR_RUN_TRIGGER_ENABLED = "true";
    process.env.MURMUR_URL = "https://murmur.example.com";
    process.env.MURMUR_TOKEN = "tok-secret";
    mockGetSessionUserId.mockReset();
    mockGetSessionUserId.mockResolvedValue(USER_ID);
    mockFetchMurmurRunStatus.mockReset();
    mockLookup.mockReset();
    mockLookup.mockResolvedValue(null);
    mockRevalidatePath.mockReset();
    mockRevalidateTag.mockReset();
  });

  afterEach(() => {
    delete process.env.MURMUR_RUN_TRIGGER_ENABLED;
    delete process.env.MURMUR_URL;
    delete process.env.MURMUR_TOKEN;
  });

  it("rejects unauthenticated calls with 401", async () => {
    mockGetSessionUserId.mockResolvedValue(null);
    const res = await GET(
      makeRequest() as never,
      makeContext("r_abc"),
    );
    expect(res.status).toBe(401);
    await expect(res.json()).resolves.toEqual({
      ok: false,
      errors: ["unauthorized"],
    });
    expect(mockFetchMurmurRunStatus).not.toHaveBeenCalled();
  });

  it("returns 503 when the feature flag is unset", async () => {
    delete process.env.MURMUR_RUN_TRIGGER_ENABLED;
    const res = await GET(makeRequest() as never, makeContext("r_abc"));
    expect(res.status).toBe(503);
    await expect(res.json()).resolves.toEqual({
      ok: false,
      errors: ["disabled"],
    });
    expect(mockFetchMurmurRunStatus).not.toHaveBeenCalled();
  });

  it("returns the small status shape when running (no slug yet)", async () => {
    mockFetchMurmurRunStatus.mockResolvedValue({
      status: "running",
      webhook_status: "pending",
    });
    mockLookup.mockResolvedValue(null);
    const res = await GET(makeRequest() as never, makeContext("r_abc"));
    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toEqual({
      ok: true,
      data: { status: "running", webhook_status: "pending" },
    });
    // Crucially: agent_actions is NEVER returned.
    expect(JSON.stringify(body)).not.toContain("agent_actions");
    expect(mockRevalidatePath).not.toHaveBeenCalled();
    expect(mockRevalidateTag).not.toHaveBeenCalled();
  });

  it("returns slug+company_id when delivered AND accept-log row exists", async () => {
    mockFetchMurmurRunStatus.mockResolvedValue({
      status: "completed",
      webhook_status: "delivered",
    });
    mockLookup.mockResolvedValue({
      slug: "anthropic",
      companyId: "00000000-0000-0000-0000-000000000001",
    });
    const res = await GET(makeRequest() as never, makeContext("r_abc"));
    expect(res.status).toBe(200);
    const body = (await res.json()) as { ok: boolean; data: Record<string, unknown> };
    expect(body.ok).toBe(true);
    expect(body.data).toEqual({
      status: "completed",
      webhook_status: "delivered",
      slug: "anthropic",
      company_id: "00000000-0000-0000-0000-000000000001",
    });
  });

  it("triggers revalidatePath + revalidateTag on the first delivered transition", async () => {
    mockFetchMurmurRunStatus.mockResolvedValue({
      status: "completed",
      webhook_status: "delivered",
    });
    mockLookup.mockResolvedValue({
      slug: "anthropic",
      companyId: "00000000-0000-0000-0000-000000000001",
    });
    await GET(makeRequest() as never, makeContext("r_abc"));
    expect(mockRevalidatePath).toHaveBeenCalledWith("/[lang]/(app)/explore");
    expect(mockRevalidateTag).toHaveBeenCalledWith("companies");
  });

  it("does NOT trigger revalidation when delivered but accept-log row is missing", async () => {
    mockFetchMurmurRunStatus.mockResolvedValue({
      status: "completed",
      webhook_status: "delivered",
    });
    mockLookup.mockResolvedValue(null);
    await GET(makeRequest() as never, makeContext("r_abc"));
    expect(mockRevalidatePath).not.toHaveBeenCalled();
    expect(mockRevalidateTag).not.toHaveBeenCalled();
  });

  it("does NOT trigger revalidation while still running", async () => {
    mockFetchMurmurRunStatus.mockResolvedValue({
      status: "running",
      webhook_status: "pending",
    });
    mockLookup.mockResolvedValue(null);
    await GET(makeRequest() as never, makeContext("r_abc"));
    expect(mockRevalidatePath).not.toHaveBeenCalled();
    expect(mockRevalidateTag).not.toHaveBeenCalled();
  });

  it("returns 502 on upstream http_4xx", async () => {
    const { RunStatusError } = await vi.importActual<
      typeof import("@/lib/murmur/run-status")
    >("@/lib/murmur/run-status");
    mockFetchMurmurRunStatus.mockRejectedValue(
      new RunStatusError("http_4xx", "404", { status: 404 }),
    );
    const res = await GET(makeRequest() as never, makeContext("r_abc"));
    expect(res.status).toBe(502);
    await expect(res.json()).resolves.toEqual({
      ok: false,
      errors: ["upstream:http_4xx"],
    });
  });

  it("returns 504 on upstream timeout", async () => {
    const { RunStatusError } = await vi.importActual<
      typeof import("@/lib/murmur/run-status")
    >("@/lib/murmur/run-status");
    mockFetchMurmurRunStatus.mockRejectedValue(
      new RunStatusError("timeout", "deadline"),
    );
    const res = await GET(makeRequest() as never, makeContext("r_abc"));
    expect(res.status).toBe(504);
    await expect(res.json()).resolves.toEqual({
      ok: false,
      errors: ["upstream:timeout"],
    });
  });

  it("does not 500 even when revalidation throws", async () => {
    mockFetchMurmurRunStatus.mockResolvedValue({
      status: "completed",
      webhook_status: "delivered",
    });
    mockLookup.mockResolvedValue({
      slug: "anthropic",
      companyId: "00000000-0000-0000-0000-000000000001",
    });
    mockRevalidatePath.mockImplementation(() => {
      throw new Error("revalidate boom");
    });
    const res = await GET(makeRequest() as never, makeContext("r_abc"));
    expect(res.status).toBe(200);
    const body = (await res.json()) as { ok: boolean };
    expect(body.ok).toBe(true);
  });
});
