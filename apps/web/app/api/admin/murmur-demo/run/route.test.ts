import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("@/lib/murmur/start-run", async () => {
  // Re-export the real StartRunError class shape so `instanceof` checks
  // inside the route work on the mocked module.
  const actual = await vi.importActual<
    typeof import("@/lib/murmur/start-run")
  >("@/lib/murmur/start-run");
  return {
    StartRunError: actual.StartRunError,
    startRun: vi.fn(),
  };
});

import { startRun, StartRunError } from "@/lib/murmur/start-run";
import { POST } from "./route";

const ADMIN_SECRET = "secret-token";
const URL_BASE = "http://localhost/api/admin/murmur-demo/run";

describe("POST /api/admin/murmur-demo/run", () => {
  beforeEach(() => {
    process.env.ADMIN_SECRET = ADMIN_SECRET;
    process.env.MURMUR_RUN_TRIGGER_ENABLED = "true";
    vi.mocked(startRun).mockReset();
  });

  afterEach(() => {
    delete process.env.MURMUR_RUN_TRIGGER_ENABLED;
  });

  it("rejects requests without basic auth (401)", async () => {
    const res = await POST(
      new Request(URL_BASE, {
        method: "POST",
        body: JSON.stringify({
          company_name: "Acme",
          website: "https://acme.example",
        }),
      }),
    );
    expect(res.status).toBe(401);
    expect(vi.mocked(startRun)).not.toHaveBeenCalled();
  });

  it("returns 503 when the feature flag is not 'true'", async () => {
    process.env.MURMUR_RUN_TRIGGER_ENABLED = "false";
    const res = await POST(
      new Request(URL_BASE, {
        method: "POST",
        headers: { Authorization: `Basic ${ADMIN_SECRET}` },
        body: JSON.stringify({
          company_name: "Acme",
          website: "https://acme.example",
        }),
      }),
    );
    expect(res.status).toBe(503);
    expect(vi.mocked(startRun)).not.toHaveBeenCalled();
  });

  it("returns 400 when the body is not valid JSON", async () => {
    const res = await POST(
      new Request(URL_BASE, {
        method: "POST",
        headers: { Authorization: `Basic ${ADMIN_SECRET}` },
        body: "not json",
      }),
    );
    expect(res.status).toBe(400);
    expect(vi.mocked(startRun)).not.toHaveBeenCalled();
  });

  it("returns 400 when company_name or website is missing/empty", async () => {
    for (const body of [
      {},
      { company_name: "" },
      { website: "https://x" },
      { company_name: "Acme", website: "" },
      { company_name: 42, website: "https://x" },
    ]) {
      const res = await POST(
        new Request(URL_BASE, {
          method: "POST",
          headers: { Authorization: `Basic ${ADMIN_SECRET}` },
          body: JSON.stringify(body),
        }),
      );
      expect(res.status).toBe(400);
    }
    expect(vi.mocked(startRun)).not.toHaveBeenCalled();
  });

  it("returns 200 with run_id on success", async () => {
    vi.mocked(startRun).mockResolvedValue({ run_id: "run_demo_001" });
    const res = await POST(
      new Request(URL_BASE, {
        method: "POST",
        headers: { Authorization: `Basic ${ADMIN_SECRET}` },
        body: JSON.stringify({
          company_name: "Acme",
          website: "https://acme.example",
        }),
      }),
    );
    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toEqual({ run_id: "run_demo_001" });
    expect(vi.mocked(startRun)).toHaveBeenCalledWith({
      company_name: "Acme",
      website: "https://acme.example",
    });
  });

  it("maps StartRunError(http_4xx) to 502", async () => {
    vi.mocked(startRun).mockRejectedValue(
      new StartRunError("http_4xx", "Murmur returned HTTP 400", { status: 400 }),
    );
    const res = await POST(
      new Request(URL_BASE, {
        method: "POST",
        headers: { Authorization: `Basic ${ADMIN_SECRET}` },
        body: JSON.stringify({
          company_name: "Acme",
          website: "https://acme.example",
        }),
      }),
    );
    expect(res.status).toBe(502);
    await expect(res.json()).resolves.toMatchObject({ code: "http_4xx" });
  });

  it("maps StartRunError(timeout) to 504", async () => {
    vi.mocked(startRun).mockRejectedValue(
      new StartRunError("timeout", "request timed out"),
    );
    const res = await POST(
      new Request(URL_BASE, {
        method: "POST",
        headers: { Authorization: `Basic ${ADMIN_SECRET}` },
        body: JSON.stringify({
          company_name: "Acme",
          website: "https://acme.example",
        }),
      }),
    );
    expect(res.status).toBe(504);
    await expect(res.json()).resolves.toMatchObject({ code: "timeout" });
  });

  it("maps StartRunError(config_missing) to 503", async () => {
    vi.mocked(startRun).mockRejectedValue(
      new StartRunError("config_missing", "missing MURMUR_URL"),
    );
    const res = await POST(
      new Request(URL_BASE, {
        method: "POST",
        headers: { Authorization: `Basic ${ADMIN_SECRET}` },
        body: JSON.stringify({
          company_name: "Acme",
          website: "https://acme.example",
        }),
      }),
    );
    expect(res.status).toBe(503);
  });
});
