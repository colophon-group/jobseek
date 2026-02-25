import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { FlagGB, FlagDE, FlagFR, FlagIT, flags, localeLabels } from "../flags";

describe("Flag components", () => {
  it("FlagGB renders an svg", () => {
    const { container } = render(<FlagGB />);
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("FlagDE renders an svg", () => {
    const { container } = render(<FlagDE />);
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("FlagFR renders an svg", () => {
    const { container } = render(<FlagFR />);
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("FlagIT renders an svg", () => {
    const { container } = render(<FlagIT />);
    expect(container.querySelector("svg")).not.toBeNull();
  });
});

describe("flags map", () => {
  it("maps all 4 locales to flag components", () => {
    expect(flags.en).toBe(FlagGB);
    expect(flags.de).toBe(FlagDE);
    expect(flags.fr).toBe(FlagFR);
    expect(flags.it).toBe(FlagIT);
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
