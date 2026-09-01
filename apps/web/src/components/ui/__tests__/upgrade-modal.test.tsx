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

function expectMotionSafeEntrance(dialog: HTMLElement) {
  const overlay = [...document.querySelectorAll<HTMLElement>("[data-state='open']")]
    .find((element) => element !== dialog && element.className.includes("bg-black/40"));

  expect(overlay).toBeTruthy();
  for (const element of [overlay!, dialog]) {
    const classes = element.className.split(/\s+/);
    expect(classes).toContain("motion-safe:data-[state=open]:animate-in");
    expect(classes).toContain("motion-safe:data-[state=open]:fade-in-0");
    expect(classes).not.toContain("data-[state=open]:animate-in");
    expect(classes).not.toContain("data-[state=open]:fade-in-0");
  }
}

describe("UpgradeModal (issue #3036)", () => {
  it("links its Upgrade CTA to /settings/billing, not /settings", () => {
    render(
      <UpgradeModal
        open={true}
        onOpenChange={() => {}}
        reason="This feature requires a subscription."
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

  it("only animates its entrance when reduced motion is not requested", () => {
    render(
      <UpgradeModal
        open={true}
        onOpenChange={() => {}}
        reason="This feature requires a subscription."
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "Upgrade required" });
    expectMotionSafeEntrance(dialog);
    const classes = dialog.className.split(/\s+/);
    expect(classes).toContain("motion-safe:data-[state=open]:zoom-in-95");
    expect(classes).not.toContain("data-[state=open]:zoom-in-95");
  });
});
