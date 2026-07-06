import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import "@/test-utils/lingui-mock";

import { FormField } from "../FormField";

describe("FormField", () => {
  it("associates hint and error text with the input", () => {
    render(
      <FormField
        label="Email"
        hint="Use your work email"
        error="Email is required"
      />,
    );

    const input = screen.getByLabelText("Email");
    const describedBy = input.getAttribute("aria-describedby");

    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(describedBy).toBeTruthy();

    const descriptions = describedBy
      ?.split(" ")
      .map((id) => document.getElementById(id)?.textContent);

    expect(descriptions).toEqual(["Use your work email", "Email is required"]);
  });
});
