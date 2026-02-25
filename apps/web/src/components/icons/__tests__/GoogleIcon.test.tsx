import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { GoogleIcon } from "../GoogleIcon";

describe("GoogleIcon", () => {
  it("renders an svg", () => {
    const { container } = render(<GoogleIcon />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
  });

  it("uses default size of 20", () => {
    const { container } = render(<GoogleIcon />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("width")).toBe("20");
    expect(svg.getAttribute("height")).toBe("20");
  });

  it("accepts custom size", () => {
    const { container } = render(<GoogleIcon size={32} />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("width")).toBe("32");
    expect(svg.getAttribute("height")).toBe("32");
  });
});
