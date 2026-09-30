import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";
const mocks = vi.hoisted(() => ({ update: vi.fn() }));
vi.mock("@/lib/actions/preferences", () => ({ updatePreferences: mocks.update }));
vi.mock("@/lib/useLocalePath", () => ({ useLocalePath: () => (path: string) => `/en${path}` }));
vi.mock("@/components/NavLink", () => ({ NavLink: ({ href, children, className }: { href: string; children: React.ReactNode; className: string }) => <a href={href} className={className}>{children}</a> }));
import { ProAnnouncementBanner } from "../ProAnnouncementBanner";
import { CookieBanner } from "../CookieBanner";
import { SessionProvider } from "../providers/SessionProvider";
import { BannerProvider } from "../providers/BannerProvider";

const owner = { id: "fixture-user", email: "fixture@example.com", name: "Fixture", emailVerified: true };
beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); mocks.update.mockResolvedValue(null); });

function mount({ paid = false, pending = false, anonymous = false, dismissed = false, cookie = false } = {}) {
  return render(<SessionProvider user={anonymous ? null : owner} plan={paid ? "unlimited" : "free"} isPending={pending} preferences={{ dismissedBanners: dismissed ? ["pro-launch-v1"] : [] }}>
    <BannerProvider>{cookie && <CookieBanner aboveBottomBar />}<ProAnnouncementBanner aboveBottomBar /></BannerProvider>
  </SessionProvider>);
}

describe("Pro announcement", () => {
  it("waits for the cookie banner, offers localized links and persists dismissal without subscribing", async () => {
    mount({ cookie: true });
    expect(await screen.findByText(/This site uses cookies/)).toBeTruthy();
    expect(screen.queryByRole("complementary", { name: "Job Seek Pro announcement" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Ok" }));
    const banner = await screen.findByRole("complementary", { name: "Job Seek Pro announcement" });
    expect(banner.className).toContain("bg-pro-gold-bg");
    expect(banner.querySelector("svg.lucide-crown")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Explore Pro" }).getAttribute("href")).toBe("/en/narrowed");
    expect(screen.getByRole("link", { name: "Product news" }).getAttribute("href")).toBe("/en/settings#product-news");
    await userEvent.click(screen.getByRole("button", { name: "Dismiss Pro announcement" }));
    await waitFor(() => expect(screen.queryByRole("complementary", { name: "Job Seek Pro announcement" })).toBeNull());
    expect(mocks.update).toHaveBeenLastCalledWith({ dismissBanner: "pro-launch-v1" });
    expect(localStorage.getItem("pro-launch-v1:fixture-user")).toBe("1");
  });
  it.each([{ paid: true }, { pending: true }, { anonymous: true }, { dismissed: true }])("does not show for ineligible or dismissed state %j", async props => {
    mount(props);
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(mocks.update).not.toHaveBeenCalled();
  });
});
