import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";
import { CurrencyModal } from "../CurrencyModal";

const currencies = ["EUR", "CHF", "USD", "GBP", "CAD", "JPY"];

describe("CurrencyModal", () => {
  it("searches the full supplied list by name and code, selects once, and restores focus", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <CurrencyModal value="CHF" currencies={currencies} onSelect={onSelect} />,
    );
    const trigger = screen.getByRole("button", { name: "Currency: CHF" });
    await user.click(trigger);
    const search = screen.getByRole("searchbox", {
      name: "Search by currency or code",
    });
    expect(
      screen.getByRole("button", { name: /Canadian Dollar.*CAD/i }),
    ).toBeTruthy();
    await user.type(search, "yen");
    expect(
      screen.queryByRole("button", { name: /Canadian Dollar/ }),
    ).toBeNull();
    await user.click(
      screen.getByRole("button", { name: /Japanese Yen.*JPY/i }),
    );
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("JPY");
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    await user.click(trigger);
    await user.type(screen.getByRole("searchbox"), "CAD");
    expect(
      screen.getByRole("button", { name: /Canadian Dollar.*CAD/i }),
    ).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(onSelect).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});
