import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { CompanyIcon } from "../CompanyIcon";

describe("CompanyIcon", () => {
  it("renders an unoptimized image when icon URL is provided", () => {
    const { container } = render(
      <CompanyIcon icon="https://example.com/icon.webp" alt="Acme" size={32} />,
    );
    const img = container.querySelector("img")!;
    expect(img).not.toBeNull();
    expect(img.getAttribute("alt")).toBe("Acme");
    expect(img.getAttribute("width")).toBe("32");
    expect(img.getAttribute("height")).toBe("32");
    // unoptimized means src is the source URL, not /_next/image?url=...
    expect(img.getAttribute("src")).toBe("https://example.com/icon.webp");
  });

  it("renders Building2 fallback when icon is null", () => {
    const { container } = render(<CompanyIcon icon={null} alt="" size={24} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.firstElementChild?.getAttribute("aria-hidden")).toBe("true");
  });

  it("forwards className additions", () => {
    const { container } = render(
      <CompanyIcon icon="https://example.com/i.webp" alt="" size={32} className="mt-0.5" />,
    );
    const img = container.querySelector("img")!;
    expect(img.className).toContain("size-8");
    expect(img.className).toContain("mt-0.5");
  });

  it("emits explicit decorative alt for icon-present case", () => {
    const { container } = render(
      <CompanyIcon icon="https://example.com/i.webp" alt="" size={16} />,
    );
    const img = container.querySelector("img")!;
    expect(img.getAttribute("alt")).toBe("");
  });

  it("emits no srcset (the point of unoptimized — single source request)", () => {
    const { container } = render(
      <CompanyIcon icon="https://example.com/i.webp" alt="Acme" size={32} />,
    );
    const img = container.querySelector("img")!;
    expect(img.getAttribute("srcset")).toBeNull();
  });
});
