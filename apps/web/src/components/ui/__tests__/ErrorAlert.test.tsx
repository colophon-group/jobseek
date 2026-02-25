import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ErrorAlert } from "../ErrorAlert";
import { SuccessAlert } from "../SuccessAlert";

describe("ErrorAlert", () => {
  it("renders nothing when message is empty", () => {
    const { container } = render(<ErrorAlert message="" />);
    expect(container.innerHTML).toBe("");
  });

  it("renders alert with message text", () => {
    render(<ErrorAlert message="Something went wrong" />);
    expect(screen.getByRole("alert").textContent).toBe("Something went wrong");
  });
});

describe("SuccessAlert", () => {
  it("renders nothing when message is empty", () => {
    const { container } = render(<SuccessAlert message="" />);
    expect(container.innerHTML).toBe("");
  });

  it("renders status with message text", () => {
    render(<SuccessAlert message="Saved successfully" />);
    expect(screen.getByRole("status").textContent).toBe("Saved successfully");
  });
});
