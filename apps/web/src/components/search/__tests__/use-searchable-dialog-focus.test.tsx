import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useSearchableDialogFocus } from "../use-searchable-dialog-focus";

function SearchableDialogHarness() {
  const [open, setOpen] = useState(false);
  const { searchInputRef, focusSearchInputOnOpen } = useSearchableDialogFocus();

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button type="button">Open filters</button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Content
          aria-describedby={undefined}
          onOpenAutoFocus={focusSearchInputOnOpen}
        >
          <Dialog.Title>Search filters</Dialog.Title>
          <Dialog.Close asChild>
            <button type="button">Close</button>
          </Dialog.Close>
          <input ref={searchInputRef} aria-label="Search options" />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

describe("useSearchableDialogFocus", () => {
  it("supports immediate typing and restores trigger focus after Escape", async () => {
    const user = userEvent.setup();
    render(<SearchableDialogHarness />);

    const trigger = screen.getByRole("button", { name: "Open filters" });
    await user.click(trigger);

    const input = screen.getByRole("textbox", { name: "Search options" });
    await waitFor(() => expect(document.activeElement).toBe(input));

    await user.keyboard("engineer");
    expect((input as HTMLInputElement).value).toBe("engineer");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});
