import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import "@/test-utils/lingui-mock";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => ({ isLoggedIn: false, isPending: false }),
}));
vi.mock("@/lib/actions/ai-filter", () => ({
  configureAiFilter: vi.fn(), createAiFilteredWatchlist: vi.fn(), disableAiFilter: vi.fn(),
}));
vi.mock("@/lib/useLocalePath", () => ({ useLocalePath: () => (path: string) => `/en${path}` }));

import { ProPitch } from "../ProPitch";

describe("ProPitch Narrowed preview", () => {
  it("explains both accepted roles and excluded roles with description excerpts", () => {
    render(<ProPitch previewOnly />);
    const preview = within(screen.getByRole("region", { name: "Narrowed results" }));
    expect(preview.queryByText("Illustrative examples")).toBeNull();
    const rows = preview.getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(rows.filter(row => within(row).queryByText("Match"))).toHaveLength(2);
    expect(rows.filter(row => within(row).queryByText("Filtered out"))).toHaveLength(2);

    const product = screen.getByText("Product Engineer").closest("li")!;
    expect(within(product).getByText("Match")).toBeTruthy();
    expect(product.textContent).toContain("Talk with users weekly");
    expect(product.textContent).toContain("no people management");

    const manager = screen.getByText("Engineering Manager").closest("li")!;
    expect(within(manager).getByText("Filtered out")).toBeTruthy();
    expect(manager.textContent).toContain("leading eight engineers");

    const infrastructure = screen.getByText("Infrastructure Engineer").closest("li")!;
    expect(within(infrastructure).getByText("Filtered out")).toBeTruthy();
    expect(infrastructure.textContent).toContain("No direct user or customer contact");
    expect(preview.queryByRole("link")).toBeNull();
  });

  it("counts only accepted examples and retains the read-only preview when reopened", () => {
    render(<ProPitch previewOnly />);
    fireEvent.click(screen.getByRole("button", { name: "All results" }));
    expect(screen.getByText("2 matches")).toBeTruthy();
    expect(screen.queryByText("4 matches")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Narrowed" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.queryByRole("button", { name: "Edit matching criteria" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove matching criteria" })).toBeNull();
  });
});
