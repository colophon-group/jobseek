import { beforeEach, describe, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ session: vi.fn(), get: vi.fn(), set: vi.fn() }));
vi.mock("@/lib/sessionCache", () => ({ getSessionUserId: mocks.session }));
vi.mock("@/lib/services/product-news", () => ({ getProductNewsPreference: mocks.get, setProductNewsPreference: mocks.set }));
import { getProductNewsSettings, setProductNewsSettings } from "../product-news";
import { PRODUCT_NEWS_CONSENT_VERSION as version } from "@/lib/product-news/policy";

beforeEach(() => { vi.clearAllMocks(); mocks.session.mockResolvedValue("owner"); });
describe("product news settings authorization", () => {
  it("uses the authenticated owner and rejects anonymous writes", async () => {
    mocks.set.mockResolvedValue({ enabled: true });
    expect(await setProductNewsSettings(true, "fr", version)).toEqual({ enabled: true });
    expect(mocks.set).toHaveBeenCalledWith("owner", true, "fr", "settings");
    mocks.session.mockResolvedValue(null);
    expect(await getProductNewsSettings()).toBeNull();
    expect(await setProductNewsSettings(true, "en", version)).toEqual({ error: "not_authenticated" });
    expect(mocks.set).toHaveBeenCalledTimes(1);
  });
  it("rejects malformed opt-ins but permits withdrawal from an older form", async () => {
    for (const value of ["true", null, 1, {}]) expect(await setProductNewsSettings(value as boolean, "en", version)).toEqual({ error: "invalid_request" });
    expect(await setProductNewsSettings(true, "xx", version)).toEqual({ error: "invalid_request" });
    expect(await setProductNewsSettings(true, "en", "old-version")).toEqual({ error: "invalid_request" });
    expect(mocks.set).not.toHaveBeenCalled();
    await setProductNewsSettings(false, "en", "old-version");
    expect(mocks.set).toHaveBeenCalledWith("owner", false, "en", "settings");
  });
});
