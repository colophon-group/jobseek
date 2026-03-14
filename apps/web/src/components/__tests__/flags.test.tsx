import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { CountryFlag, LocaleFlag, localeLabels } from "../flags";

describe("CountryFlag", () => {
  it("renders an img with correct src", () => {
    const { container } = render(<CountryFlag iso="us" size={20} />);
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img!.getAttribute("src")).toBe("/flags/us.svg");
    expect(img!.getAttribute("width")).toBe("20");
    expect(img!.getAttribute("height")).toBe("15");
  });

  it("returns null for empty iso", () => {
    const { container } = render(<CountryFlag iso="" />);
    expect(container.innerHTML).toBe("");
  });
});

describe("LocaleFlag", () => {
  it("renders gb flag for en locale", () => {
    const { container } = render(<LocaleFlag locale="en" />);
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img!.getAttribute("src")).toBe("/flags/gb.svg");
  });

  it("renders de flag for de locale", () => {
    const { container } = render(<LocaleFlag locale="de" />);
    const img = container.querySelector("img");
    expect(img!.getAttribute("src")).toBe("/flags/de.svg");
  });

  it("returns null for unknown locale", () => {
    const { container } = render(<LocaleFlag locale="zz" />);
    expect(container.innerHTML).toBe("");
  });
});

describe("localeLabels", () => {
  it("has correct labels for all locales", () => {
    expect(localeLabels.en).toBe("English");
    expect(localeLabels.de).toBe("Deutsch");
    expect(localeLabels.fr).toBe("Français");
    expect(localeLabels.it).toBe("Italiano");
  });
});
