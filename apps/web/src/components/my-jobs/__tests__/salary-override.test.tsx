import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { SalaryOverride } from "../salary-override";

vi.mock("@lingui/react/macro", () => ({
  useLingui: () => ({
    i18n: { locale: "de-DE" },
    t: ({ message }: { message?: string }) => message ?? "",
  }),
  Trans: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

describe("SalaryOverride", () => {
  it("formats crawler salary placeholders with the active UI locale", () => {
    render(
      <SalaryOverride
        crawlerSalary={{
          min: 1234567,
          max: 2345678,
          currency: "EUR",
          period: "yearly",
        }}
        override={{
          min: null,
          max: null,
          currency: null,
          period: null,
        }}
        onSave={vi.fn()}
      />,
    );

    expect(screen.getByPlaceholderText("1.234.567")).toBeTruthy();
    expect(screen.getByPlaceholderText("2.345.678")).toBeTruthy();
  });
});
