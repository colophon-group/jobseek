import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { NavLink } from "../NavLink";

// Stub `next/link` so React's renderer doesn't pull in router internals
// (no App Router instrumentation in this unit test). The stub preserves
// the `onClick` handler so we can assert the wired call. `prefetch` is
// dropped — passing it to a plain `<a>` triggers a React warning about
// non-boolean attributes.
vi.mock("next/link", () => ({
  __esModule: true,
  default: ({ href, onClick, children, prefetch: _prefetch, ...rest }: {
    href: string;
    onClick?: (e: React.MouseEvent) => void;
    children: React.ReactNode;
    prefetch?: boolean;
  }) => (
    <a href={href} onClick={onClick} {...rest}>{children}</a>
  ),
}));

describe("NavLink (#3046)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("calls window.scrollTo on click for plain hrefs", () => {
    const spy = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    render(<NavLink href="/en/blog">Blog</NavLink>);
    fireEvent.click(screen.getByText("Blog"));
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith({ top: 0, left: 0, behavior: "instant" });
  });

  it("skips window.scrollTo when href contains a hash anchor", () => {
    const spy = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    render(<NavLink href="/en/#features">Features</NavLink>);
    fireEvent.click(screen.getByText("Features"));
    expect(spy).not.toHaveBeenCalled();
  });

  it("forwards className and other Link props through", () => {
    render(
      <NavLink href="/en/license" className="link-class" prefetch={false}>
        License
      </NavLink>,
    );
    const anchor = screen.getByText("License") as HTMLAnchorElement;
    expect(anchor.getAttribute("href")).toBe("/en/license");
    expect(anchor.className).toContain("link-class");
  });
});
