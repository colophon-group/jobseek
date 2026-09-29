import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({ join: vi.fn(), user: null as { email: string } | null }));
vi.mock("@/lib/actions/pro-waitlist", () => ({ joinProWaitlist: mocks.join }));
vi.mock("@/components/providers/SessionProvider", () => ({ useSession: () => ({ user: mocks.user }) }));
vi.mock("@/lib/useLocalePath", () => ({ useLocalePath: () => (path: string) => `/en${path}` }));
vi.mock("@/components/pro/ProPitch", () => ({ ProPitch: () => null, FreeAccessNote: () => null }));

import { ProWaitlistForm } from "../ProWaitlistForm";
import { Pricing } from "@/components/Pricing";

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  mocks.user = null;
  mocks.join.mockResolvedValue({ success: true });
});

describe("ProWaitlistForm", () => {
  it("lets an anonymous visitor sign up and displays confirmation", async () => {
    render(<ProWaitlistForm />);
    await userEvent.type(screen.getByLabelText("Email address"), "reader@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Join the waiting list" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("You’re on the list."));
    expect(mocks.join).toHaveBeenCalledWith("reader@example.com", "en");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("prefills the signed-in email but lets the visitor choose another", async () => {
    mocks.user = { email: "account@example.com" };
    render(<ProWaitlistForm />);
    const input = screen.getByLabelText("Email address") as HTMLInputElement;
    expect(input.value).toBe("account@example.com");
    await userEvent.clear(input);
    await userEvent.type(input, "other@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Join the waiting list" }));
    await waitFor(() => expect(mocks.join).toHaveBeenCalledWith("other@example.com", "en"));
  });

  it("requires a valid email before submitting", async () => {
    render(<ProWaitlistForm />);
    await userEvent.click(screen.getByRole("button", { name: "Join the waiting list" }));
    expect(mocks.join).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText("Email address"), "not-an-email");
    await userEvent.click(screen.getByRole("button", { name: "Join the waiting list" }));
    expect(mocks.join).not.toHaveBeenCalled();
  });

  it("prevents resubmission while saving", async () => {
    let resolve!: (value: { success: true }) => void;
    mocks.join.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<ProWaitlistForm />);
    await userEvent.type(screen.getByLabelText("Email address"), "reader@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Join the waiting list" }));
    const button = screen.getByRole("button", { name: "Joining…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    await userEvent.click(button);
    expect(mocks.join).toHaveBeenCalledTimes(1);
    await act(async () => resolve({ success: true }));
  });

  it.each([
    ["invalid_email", "Enter a valid email address."],
    ["rate_limited", "Too many attempts. Please try again in an hour."],
    ["unavailable", "We couldn’t save your signup. Please try again."],
  ])("shows %s without losing the email and allows retry", async (error, message) => {
    mocks.join.mockResolvedValueOnce({ error });
    render(<ProWaitlistForm />);
    await userEvent.type(screen.getByLabelText("Email address"), "reader@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Join the waiting list" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(message));
    expect((screen.getByLabelText("Email address") as HTMLInputElement).value).toBe("reader@example.com");
    expect(screen.queryByRole("status")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Join the waiting list" }));
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
  });

  it("handles a failed network request", async () => {
    mocks.join.mockRejectedValueOnce(new Error("Network failure"));
    render(<ProWaitlistForm />);
    await userEvent.type(screen.getByLabelText("Email address"), "reader@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Join the waiting list" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Please try again."));
  });

  it("shows the waiting list on public pricing while checkout is closed", () => {
    render(<Pricing checkoutEnabled={false} />);
    expect(screen.getByRole("form", { name: "Pro waiting list" })).toBeTruthy();
  });

  it("links to billing instead when checkout opens", () => {
    render(<Pricing checkoutEnabled />);
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.getByRole("link", { name: "Learn about Pro" }).getAttribute("href")).toBe("/en/settings/billing");
  });
});
