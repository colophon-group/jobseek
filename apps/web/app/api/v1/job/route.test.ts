import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

vi.mock("@/lib/rate-limit", () => ({
  apiLimiter: {
    limit: vi.fn(async () => {
      throw new Error("no redis in unit tests");
    }),
  },
  getClientIp: () => "test-ip",
}));

const mocks = vi.hoisted(() => ({
  getPostingDetail: vi.fn(),
}));

vi.mock("@/lib/services/search", () => ({
  getPostingDetail: mocks.getPostingDetail,
}));

import { GET } from "./route";

function makeReq(qs: string): NextRequest {
  return new NextRequest(`http://localhost/api/v1/job${qs}`);
}

async function callRoute(qs: string) {
  const res = await GET(makeReq(qs));
  const body = (await res.json()) as { error?: string };
  return { res, body };
}

describe("GET /api/v1/job status contract (#3213)", () => {
  beforeEach(() => {
    mocks.getPostingDetail.mockReset();
  });

  it("returns 400 when the required `id` param is missing", async () => {
    const { res, body } = await callRoute("?locale=en");

    expect(res.status).toBe(400);
    expect(body.error).toBe("Missing required parameter: id");
    expect(mocks.getPostingDetail).not.toHaveBeenCalled();
  });

  it("returns 404 when the requested posting is not found", async () => {
    mocks.getPostingDetail.mockResolvedValue(null);

    const { res, body } = await callRoute("?id=missing&locale=de");

    expect(res.status).toBe(404);
    expect(body.error).toBe("Job posting not found");
    expect(mocks.getPostingDetail).toHaveBeenCalledWith({
      postingId: "missing",
      locale: "de",
    });
  });
});
