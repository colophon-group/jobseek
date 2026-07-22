import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  deleteUser: vi.fn(),
}));

vi.mock("@/lib/auth-client", () => ({
  authClient: {
    deleteUser: mocks.deleteUser,
  },
}));

import { DeleteAccountSection } from "../DeleteAccountSection";

async function openDeleteDialog(user: ReturnType<typeof userEvent.setup>) {
  render(<DeleteAccountSection />);
  const trigger = screen.getByRole("button", { name: "Delete my account" });
  await user.click(trigger);
  const dialog = await screen.findByRole("alertdialog", { name: "Delete account" });
  return { dialog, trigger };
}

describe("DeleteAccountSection confirmation focus", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.deleteUser.mockResolvedValue({ error: { message: "Deletion unavailable" } });
  });

  it("opens a labelled alert dialog with safe initial focus", async () => {
    const user = userEvent.setup();
    const { dialog } = await openDeleteDialog(user);

    const descriptionId = dialog.getAttribute("aria-describedby");
    expect(descriptionId).toBeTruthy();
    expect(document.getElementById(descriptionId!)).toHaveProperty(
      "textContent",
      "Permanently delete your account and all associated data. This action cannot be undone.",
    );
    await waitFor(() => {
      expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Cancel" }));
    });
  });

  it("restores focus to Delete my account after Cancel", async () => {
    const user = userEvent.setup();
    const { dialog, trigger } = await openDeleteDialog(user);

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
      expect(document.activeElement).toBe(trigger);
    });
  });

  it("restores focus to Delete my account after Escape", async () => {
    const user = userEvent.setup();
    const { trigger } = await openDeleteDialog(user);

    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
      expect(document.activeElement).toBe(trigger);
    });
  });

  it("preserves the confirmed deletion request and keeps failures actionable", async () => {
    const user = userEvent.setup();
    const { dialog } = await openDeleteDialog(user);

    await user.click(within(dialog).getByRole("button", { name: "Confirm deletion" }));

    await waitFor(() => {
      expect(mocks.deleteUser).toHaveBeenCalledWith({});
      expect(within(dialog).getByText("Deletion unavailable")).toBeTruthy();
    });
  });
});
