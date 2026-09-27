import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";
const mocks = vi.hoisted(() => ({ save: vi.fn(), refresh: vi.fn() }));
vi.mock("@/lib/actions/notifications", () => ({ setNotificationsPaused: mocks.save }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: mocks.refresh }) }));
import { NotificationSettings } from "../NotificationSettings";
beforeEach(() => vi.clearAllMocks());
describe("NotificationSettings", () => {
  it("persists pause/resume and explains account email and no catch-up", async () => {
    const user = userEvent.setup();
    mocks.save.mockResolvedValueOnce({ notificationsPaused: true }).mockResolvedValueOnce({ notificationsPaused: false });
    render(<NotificationSettings paused={false} verified />);
    const toggle = screen.getByRole("switch", { name: "Pause all email notifications" });
    await user.click(toggle);
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("true"));
    expect(mocks.save).toHaveBeenLastCalledWith(true);
    expect(screen.getByText(/Jobs from the paused period/)).toBeTruthy();
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
