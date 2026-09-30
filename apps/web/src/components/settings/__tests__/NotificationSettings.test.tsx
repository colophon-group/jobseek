import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";
const mocks = vi.hoisted(() => ({ mode: vi.fn(), save: vi.fn(), refresh: vi.fn() }));
vi.mock("@/lib/actions/notifications", () => ({ setNotificationsPaused: mocks.save, setWatchlistNotificationMode: mocks.mode }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: mocks.refresh }), useParams: () => ({ lang: "en" }) }));
import { NotificationSettings } from "../NotificationSettings";
beforeEach(() => vi.clearAllMocks());
describe("NotificationSettings", () => {
  it("persists pause/resume and explains account email and no catch-up", async () => {
    const user = userEvent.setup();
    mocks.save.mockResolvedValueOnce({ notificationsPaused: true }).mockResolvedValueOnce({ notificationsPaused: false });
    render(<NotificationSettings paused={false} verified />);
    const toggle = screen.getByRole("switch", { name: "Pause all" });
    await user.click(toggle);
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("true"));
    expect(mocks.save).toHaveBeenLastCalledWith(true);
    expect(screen.getByText(/Paused. Your watchlist choices are saved/)).toBeTruthy();
    await user.click(screen.getByText("How weekly emails work"));
    expect(screen.getByText(/without jobs from the paused period/)).toBeTruthy();
    expect(screen.getByText(/password-reset emails still arrive/)).toBeTruthy();
    await user.click(toggle);
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("false"));
    expect(mocks.save).toHaveBeenLastCalledWith(false);
  });
  it("preserves the prior choice when saving fails and explains verification", async () => {
    mocks.save.mockRejectedValue(new Error("offline"));
    render(<NotificationSettings paused verified={false} />);
    const toggle = screen.getByRole("switch");
    await userEvent.click(toggle);
    expect(await screen.findByText("Could not save your preference. Please try again.")).toBeTruthy();
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(/Verify your email/)).toBeTruthy();
  });
});

const lists = [
  { id: "one", title: "Engineering", mode: "all" as const, prompt: "Senior backend roles with distributed systems", narrowingAvailable: true, filterPreview: { filters: { keywords: ["Backend"], locationSlugs: ["switzerland"], workMode: ["remote" as const], technologySlugs: ["python"] }, locations: [{ id: 1, slug: "switzerland", name: "Switzerland", type: "country" as const, parentName: null }] } },
  { id: "two", title: "Design", mode: "off" as const, prompt: null, narrowingAvailable: false },
];
describe("watchlist contribution to the consolidated email", () => {
  it("opens full narrowing details and saves only the selected watchlist's scope", async () => {
    const user = userEvent.setup();
    mocks.mode.mockResolvedValue({ mode: "narrowed" });
    render(<NotificationSettings paused={false} verified watchlists={lists} />);
    expect(screen.queryByText(lists[0]!.prompt!)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Narrowing filters" }));
    expect(within(screen.getByRole("dialog")).getByText(lists[0]!.prompt!)).toBeTruthy();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Engineering: All results" }));
    await user.click(screen.getByRole("menuitemradio", { name: /Narrowed/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Engineering: Narrowed" })).toBeTruthy());
    expect(mocks.mode).toHaveBeenCalledWith("one", "narrowed");
    expect(screen.getByRole("link", { name: "Engineering" }).getAttribute("href")).toBe("/en/watchlists/one");
    await user.click(screen.getByRole("button", { name: "Design: Off" }));
    expect(screen.getByRole("menuitemradio", { name: /Narrowed/ }).getAttribute("aria-disabled")).toBe("true");
    await user.keyboard("{Escape}");
    expect(screen.getByText("Switzerland")).toBeTruthy();
    expect(screen.getByText("Backend")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Show all filters" }));
    expect(screen.getByText("Remote")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Engineering: Narrowed" }));
    mocks.mode.mockResolvedValue({ mode: "off" });
    await user.click(screen.getByRole("menuitemradio", { name: /Off/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Engineering: Off" })).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "Narrowing filters" }));
    expect(within(screen.getByRole("dialog")).getByText(lists[0]!.prompt!)).toBeTruthy();
  });
  it("preserves the prior selection on failure and disables choices under global pause", async () => {
    const user = userEvent.setup();
    mocks.mode.mockResolvedValue({ error: "narrowing_unavailable" });
    const view = render(<NotificationSettings paused={false} verified watchlists={lists} />);
    await user.click(screen.getByRole("button", { name: "Engineering: All results" }));
    await user.click(screen.getByRole("menuitemradio", { name: /Narrowed/ }));
    expect(await screen.findByText(/Enable narrowing with an active subscription/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Engineering: All results" })).toBeTruthy();
    view.rerender(<NotificationSettings paused verified watchlists={lists} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Engineering: All results" }) as HTMLButtonElement).disabled).toBe(true));
    expect((screen.getByRole("button", { name: "Design: Off" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
