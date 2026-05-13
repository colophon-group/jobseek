/**
 * Tests for the upgrade modal — issue #3036 sub-bug 3.
 *
 * The CTA used to link to `/settings`, dropping users on the General
 * tab unrelated to plans. Lock the destination to `/settings/billing`
 * so we don't silently regress.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@/test-utils/lingui-mock";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: Record<string, unknown>) => (
    <a href={href as string} {...props}>{children as React.ReactNode}</a>
  ),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (p: string) => `/en${p}`,
}));

import { UpgradeModal } from "../upgrade-modal";

describe("UpgradeModal (issue #3036)", () => {
  it("links its Upgrade CTA to /settings/billing, not /settings", () => {
    render(
      <UpgradeModal
        open={true}
        onOpenChange={() => {}}
        reason="You've reached your watchlist limit."
      />,
    );

    const link = screen.getByRole("link", { name: /upgrade/i });
    expect(link.getAttribute("href")).toBe("/en/settings/billing");
    // Negative check: the broken pre-fix URL would land users on the
    // wrong tab.
    expect(link.getAttribute("href")).not.toBe("/en/settings");
  });

  it("renders the reason text passed in", () => {
    render(
      <UpgradeModal
        open={true}
        onOpenChange={() => {}}
        reason="custom reason xyz"
      />,
    );
    expect(screen.getByText("custom reason xyz")).toBeTruthy();
  });
});
