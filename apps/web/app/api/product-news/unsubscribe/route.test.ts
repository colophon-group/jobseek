// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({ unsubscribe: vi.fn() }));
vi.mock("@/lib/services/product-news", () => ({ unsubscribeProductNews: mocks.unsubscribe }));
import { GET, POST } from "./route";
beforeEach(() => { vi.clearAllMocks(); mocks.unsubscribe.mockResolvedValue({ locale: "de", superseded: false }); });
describe("marketing unsubscribe route", () => {
  it("GET never opts out and renders a localized confirmation with privacy headers", async () => {
    const response = await GET(new Request("https://jseek.co/api/product-news/unsubscribe?token=opaque"));
    expect(mocks.unsubscribe).toHaveBeenCalledWith("opaque", false);
    expect(await response.text()).toContain("Produktneuigkeiten abbestellen?");
    expect(response.headers.get("Cache-Control")).toContain("no-store");
    expect(response.headers.get("Referrer-Policy")).toBe("no-referrer");
    expect(response.headers.get("X-Robots-Tag")).toBe("noindex, nofollow");
  });
  it("supports one-click POST without an account session", async () => {
    const response = await POST(new Request("https://jseek.co/api/product-news/unsubscribe?token=opaque", { method: "POST", body: "List-Unsubscribe=One-Click" }));
    expect(response.status).toBe(200);
    expect(mocks.unsubscribe).toHaveBeenCalledWith("opaque", true);
    expect(await response.text()).toContain("Du bist abgemeldet");
  });
  it("rejects malformed and oversized submissions without a write", async () => {
    expect((await POST(new Request("https://jseek.co/api/product-news/unsubscribe", { method: "POST", body: "unsubscribe=true" }))).status).toBe(400);
    expect((await POST(new Request("https://jseek.co/api/product-news/unsubscribe", { method: "POST", headers: { "Content-Length": "2048" } }))).status).toBe(413);
    expect(mocks.unsubscribe).not.toHaveBeenCalled();
  });
  it("does not claim withdrawal for a superseded consent; retries database failures", async () => {
    mocks.unsubscribe.mockResolvedValueOnce({ locale: "en", superseded: true });
    const response = await POST(new Request("https://jseek.co/api/product-news/unsubscribe?token=old", { method: "POST", body: "List-Unsubscribe=One-Click" }));
    expect(await response.text()).toContain("Your preference changed");
    mocks.unsubscribe.mockRejectedValueOnce(new Error("database fixture unavailable"));
    expect((await GET(new Request("https://jseek.co/api/product-news/unsubscribe?token=opaque"))).status).toBe(503);
  });
});
