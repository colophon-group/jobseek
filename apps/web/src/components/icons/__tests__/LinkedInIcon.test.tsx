import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { LinkedInIcon } from "../LinkedInIcon";

describe("LinkedInIcon", () => {
  it("renders an svg", () => {
    const { container } = render(<LinkedInIcon />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
  });

  it("uses default size of 20", () => {
    const { container } = render(<LinkedInIcon />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("width")).toBe("20");
    expect(svg.getAttribute("height")).toBe("20");
  });

  it("accepts custom size", () => {
    const { container } = render(<LinkedInIcon size={24} />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("width")).toBe("24");
    expect(svg.getAttribute("height")).toBe("24");
  });
});
