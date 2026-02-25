import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: Record<string, unknown>) => (
    <a href={href as string} {...props}>{children as React.ReactNode}</a>
  ),
}));

import { Button } from "../Button";

describe("Button", () => {
  it("renders a <button> when no href", () => {
    render(<Button>Click me</Button>);
    const btn = screen.getByRole("button");
    expect(btn.tagName).toBe("BUTTON");
    expect(btn.textContent).toBe("Click me");
  });

  it("renders a link when href is provided", () => {
    render(<Button href="/test">Go</Button>);
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("/test");
    expect(link.textContent).toBe("Go");
  });

  it("applies primary variant by default", () => {
    render(<Button>Test</Button>);
    const btn = screen.getByRole("button");
    expect(btn.className).toContain("bg-primary");
  });

  it("applies outline variant", () => {
    render(<Button variant="outline">Test</Button>);
    const btn = screen.getByRole("button");
    expect(btn.className).toContain("border-current");
  });

  it("applies danger variant", () => {
    render(<Button variant="danger">Test</Button>);
    const btn = screen.getByRole("button");
    expect(btn.className).toContain("text-error");
    expect(btn.className).toContain("bg-error-border");
  });

  it("applies sm size", () => {
    render(<Button size="sm">Test</Button>);
    const btn = screen.getByRole("button");
    expect(btn.className).toContain("px-4");
    expect(btn.className).toContain("py-1.5");
  });

  it("applies md size by default", () => {
    render(<Button>Test</Button>);
    const btn = screen.getByRole("button");
    expect(btn.className).toContain("px-5");
    expect(btn.className).toContain("py-2");
  });

  it("merges custom className", () => {
    render(<Button className="my-custom">Test</Button>);
    const btn = screen.getByRole("button");
    expect(btn.className).toContain("my-custom");
  });
});
