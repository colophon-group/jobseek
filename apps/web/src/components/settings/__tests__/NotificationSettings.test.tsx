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
  it("previews the saved prompt and saves only the selected watchlist's scope", async () => {
    mocks.mode.mockResolvedValue({ mode: "narrowed" });
    render(<NotificationSettings paused={false} verified watchlists={lists} />);
    expect(screen.getByText(/One email with results from all your enabled watchlists/)).toBeTruthy();
    expect(screen.getByText(lists[0]!.prompt!)).toBeTruthy();
    const narrowed = within(screen.getByRole("group", { name: "Engineering" })).getByRole("radio", { name: "Narrowed" });
    await userEvent.click(narrowed);
    await waitFor(() => expect((narrowed as HTMLInputElement).checked).toBe(true));
    expect(mocks.mode).toHaveBeenCalledWith("one", "narrowed");
    expect(screen.getByText(lists[0]!.prompt!)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Engineering" }).getAttribute("href")).toBe("/en/watchlists/one");
    expect(screen.queryByRole("button", { name: /share|delete/i })).toBeNull();
    expect(within(screen.getByRole("group", { name: "Design" })).getByRole("radio", { name: "Narrowed" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("Switzerland")).toBeTruthy();
    expect(screen.getByText("Backend")).toBeTruthy();
    const expand = screen.getByRole("button", { name: "Show all filters" });
    await userEvent.click(expand);
    expect(screen.getByText("Remote")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Show fewer filters" })).toBe(expand);
    mocks.mode.mockResolvedValue({ mode: "off" });
    const off = within(screen.getByRole("group", { name: "Engineering" })).getByRole("radio", { name: "Off" });
    await userEvent.click(off);
    await waitFor(() => expect((off as HTMLInputElement).checked).toBe(true));
    expect(screen.getByText(lists[0]!.prompt!)).toBeTruthy();
  });
  it("preserves the prior selection on failure and disables choices under global pause", async () => {
    mocks.mode.mockResolvedValue({ error: "narrowing_unavailable" });
    const view = render(<NotificationSettings paused={false} verified watchlists={lists} />);
    await userEvent.click(within(screen.getByRole("group", { name: "Engineering" })).getByRole("radio", { name: "Narrowed" }));
    expect(await screen.findByText(/Enable narrowing with an active subscription/)).toBeTruthy();
    expect((within(screen.getByRole("group", { name: "Engineering" })).getByRole("radio", { name: "All results" }) as HTMLInputElement).checked).toBe(true);
    view.rerender(<NotificationSettings paused verified watchlists={lists} />);
    await waitFor(() => expect(lists.every(list => screen.getByRole("group", { name: list.title }).hasAttribute("disabled"))).toBe(true));
  });
});
