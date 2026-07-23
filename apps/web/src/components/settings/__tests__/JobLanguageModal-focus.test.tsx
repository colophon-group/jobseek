import { useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";
import { JobLanguageModal } from "../JobLanguageModal";

const selectedLanguages = new Set(["en"]);
const availableLanguages = new Set(["de", "en"]);
const toggleLanguage = vi.fn();

function JobLanguageModalHarness() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Find more
      </button>
      <JobLanguageModal
        open={open}
        onOpenChange={setOpen}
        selected={selectedLanguages}
        onToggle={toggleLanguage}
        availableCodes={availableLanguages}
      />
    </>
  );
}

describe("JobLanguageModal focus lifecycle (#5990)", () => {
  it("supports immediate typing and restores the external trigger after Escape", async () => {
    const user = userEvent.setup();
    render(<JobLanguageModalHarness />);

    const trigger = screen.getByRole("button", { name: "Find more" });
    await user.click(trigger);

    const search = screen.getByRole("textbox", {
      name: "Search languages...",
    });
    await waitFor(() => expect(document.activeElement).toBe(search));

    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog)
        .getByRole("button", { name: "English" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      within(dialog)
        .getByRole("button", { name: "Deutsch" })
        .getAttribute("aria-pressed"),
    ).toBe("false");

    await user.keyboard("de");
    expect((search as HTMLInputElement).value).toBe("de");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));

    await user.click(trigger);
    const reopenedSearch = screen.getByRole("textbox", {
      name: "Search languages...",
    });
    await waitFor(() => expect(document.activeElement).toBe(reopenedSearch));
    expect((reopenedSearch as HTMLInputElement).value).toBe("");
  });
});
