import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";
const mocks = vi.hoisted(() => ({ save: vi.fn(), refresh: vi.fn() }));
vi.mock("@/lib/actions/product-news", () => ({ setProductNewsSettings: mocks.save }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: mocks.refresh }) }));
import { ProductNewsSettings } from "../ProductNewsSettings";

beforeEach(() => vi.clearAllMocks());
describe("product-news preference UI", () => {
  it("starts unchecked and persists affirmative choice and withdrawal independently", async () => {
    const user = userEvent.setup();
    mocks.save.mockResolvedValueOnce({ enabled: true }).mockResolvedValueOnce({ enabled: false });
    render(<ProductNewsSettings enabled={false} verified />);
    const checkbox = screen.getByRole("checkbox") as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    expect(screen.getByText(/separate from your weekly job emails/)).toBeTruthy();
    await user.click(checkbox);
    await waitFor(() => expect(checkbox.checked).toBe(true));
    expect(mocks.save).toHaveBeenLastCalledWith(true, "en", "product-news-v1");
    await user.click(checkbox);
    await waitFor(() => expect(checkbox.checked).toBe(false));
    expect(mocks.save).toHaveBeenLastCalledWith(false, "en", "product-news-v1");
  });
  it("retains the saved choice on failure and lets an unverified owner withdraw", async () => {
    mocks.save.mockRejectedValue(new Error("offline"));
    render(<ProductNewsSettings enabled verified={false} />);
    const checkbox = screen.getByRole("checkbox") as HTMLInputElement;
    await userEvent.click(checkbox);
    expect(await screen.findByText("Could not save your preference. Please try again.")).toBeTruthy();
    expect(checkbox.checked).toBe(true);
    expect(screen.getByText(/Verify your email address/)).toBeTruthy();
  });
});
