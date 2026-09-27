import { beforeEach, describe, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ unsubscribe: vi.fn() }));
vi.mock("@/lib/services/notification-unsubscribe", () => ({ unsubscribeNotification: mocks.unsubscribe }));
import { GET, POST } from "./route";
beforeEach(() => mocks.unsubscribe.mockReset().mockResolvedValue({ locale: "de" }));
describe("unsubscribe HTTP contract", () => {
  it("does not unsubscribe link scanners and renders a localized confirmation", async () => {
    const response = await GET(new Request("https://jseek.co/api/notifications/unsubscribe?token=signed"));
    expect(mocks.unsubscribe).toHaveBeenCalledWith("signed", false);
    expect(await response.text()).toContain("Wöchentliche Job-E-Mails pausieren?");
    expect(response.headers.get("Referrer-Policy")).toBe("no-referrer");
  });
  it("accepts standard one-click POST without cookies or login", async () => {
    const response = await POST(new Request("https://jseek.co/api/notifications/unsubscribe?token=signed", { method: "POST", body: "List-Unsubscribe=One-Click", headers: { "Content-Type": "application/x-www-form-urlencoded" } }));
    expect(response.status).toBe(200);
    expect(mocks.unsubscribe).toHaveBeenCalledWith("signed", true);
  });
  it("rejects malformed POSTs and invalid capabilities", async () => {
    expect((await POST(new Request("https://jseek.co/api/notifications/unsubscribe", { method: "POST", body: "arbitrary" }))).status).toBe(400);
    expect(mocks.unsubscribe).not.toHaveBeenCalled();
    mocks.unsubscribe.mockResolvedValue(null);
    expect((await GET(new Request("https://jseek.co/api/notifications/unsubscribe?token=bad"))).status).toBe(400);
  });
});
