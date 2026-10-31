import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({ updatePreferences: vi.fn() }));
vi.mock("@/lib/actions/preferences", () => ({
  updatePreferences: mocks.updatePreferences,
}));

import { ServiceShutdownNotice } from "../ServiceShutdownNotice";
import { SessionProvider } from "../providers/SessionProvider";

const dismissalKey = "service-shutdown-notice-v1";
const noticeName = "A quick heads-up";
const owner = {
  id: "fixture-user",
  email: "fixture@example.com",
  name: "Fixture",
  emailVerified: true,
};

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ServiceShutdownNotice", () => {
  it("tells new visitors about the shutdown and free month without a session provider", async () => {
    render(<ServiceShutdownNotice />);

    const notice = await screen.findByRole("complementary", { name: noticeName });
    expect(within(notice).getByRole("heading", { name: noticeName }).tagName).toBe("H2");
    expect(notice.textContent).toContain("Jobseek is going to shut down.");
    expect(notice.textContent).toContain("Thank you for being here");
    expect(notice.textContent).toContain("one month free on our next service when it’s ready");
    expect(within(notice).queryAllByRole("link")).toHaveLength(0);
    expect(within(notice).getAllByRole("button")).toHaveLength(1);
    expect(within(notice).getByRole("button", { name: "Got it" })).toBeTruthy();
    expect(notice.textContent).not.toMatch(/Explore Pro|Product news|7-day|trial|pricing/i);
  });

  it.each([
    { audience: "anonymous visitors", user: null, plan: "free", pending: false },
    { audience: "signed-in visitors", user: owner, plan: "free", pending: false },
    { audience: "paid subscribers", user: owner, plan: "unlimited", pending: false },
    { audience: "visitors with a pending session", user: null, plan: "free", pending: true },
  ] as const)("shows for $audience regardless of account banner preferences", async ({ user, plan, pending }) => {
    render(
      <SessionProvider
        user={user}
        plan={plan}
        isPending={pending}
        preferences={{ dismissedBanners: ["cookie-consent", "pro-launch-v1"] }}
      >
        <ServiceShutdownNotice />
      </SessionProvider>,
    );

    expect(await screen.findByRole("complementary", { name: noticeName })).toBeTruthy();
  });

  it("persists dismissal across remounts", async () => {
    const firstVisit = render(<ServiceShutdownNotice />);
    await screen.findByRole("complementary", { name: noticeName });

    await userEvent.click(screen.getByRole("button", { name: "Got it" }));

    expect(screen.queryByRole("complementary", { name: noticeName })).toBeNull();
    expect(localStorage.getItem(dismissalKey)).toBe("1");

    firstVisit.unmount();
    render(<ServiceShutdownNotice />);

    expect(screen.queryByRole("complementary", { name: noticeName })).toBeNull();
  });

  it("keeps cookie and Pro dismissal keys independent", async () => {
    localStorage.setItem("cookie-consent", "1");
    localStorage.setItem("pro-launch-v1:fixture-user", "1");
    const firstVisit = render(<ServiceShutdownNotice />);

    expect(await screen.findByRole("complementary", { name: noticeName })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Got it" }));

    expect(localStorage.getItem("cookie-consent")).toBe("1");
    expect(localStorage.getItem("pro-launch-v1:fixture-user")).toBe("1");

    firstVisit.unmount();
    localStorage.removeItem("cookie-consent");
    localStorage.removeItem("pro-launch-v1:fixture-user");
    render(<ServiceShutdownNotice />);

    expect(screen.queryByRole("complementary", { name: noticeName })).toBeNull();
  });

  it("still shows and dismisses for this visit when browser storage is denied", async () => {
    const read = vi.fn(() => { throw new DOMException("Storage access denied", "SecurityError"); });
    const write = vi.fn(() => { throw new DOMException("Storage access denied", "SecurityError"); });
    vi.stubGlobal("localStorage", { getItem: read, setItem: write });
    const firstVisit = render(<ServiceShutdownNotice />);

    expect(await screen.findByRole("complementary", { name: noticeName })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Got it" }));

    expect(screen.queryByRole("complementary", { name: noticeName })).toBeNull();
    expect(read).toHaveBeenCalled();
    expect(write).toHaveBeenCalled();

    firstVisit.unmount();
    render(<ServiceShutdownNotice />);
    expect(await screen.findByRole("complementary", { name: noticeName })).toBeTruthy();
  });

  it("dismisses without cookie, consent, or API side effects", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const cookieBefore = document.cookie;
    render(<ServiceShutdownNotice />);
    await screen.findByRole("complementary", { name: noticeName });

    await userEvent.click(screen.getByRole("button", { name: "Got it" }));

    expect(screen.queryByRole("complementary", { name: noticeName })).toBeNull();
    expect(document.cookie).toBe(cookieBefore);
    expect(localStorage.length).toBe(1);
    expect(localStorage.getItem(dismissalKey)).toBe("1");
    expect(localStorage.getItem("cookie-consent")).toBeNull();
    expect(localStorage.getItem("pro-launch-v1:fixture-user")).toBeNull();
    expect(mocks.updatePreferences).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });
});
