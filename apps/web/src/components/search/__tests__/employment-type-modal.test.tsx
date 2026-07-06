import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// Lingui shim — register before imports of Lingui-aware modules.
import "@/test-utils/lingui-mock";

const getEmploymentTypeCountsMock = vi.fn();
vi.mock("@/lib/actions/taxonomy", () => ({
  getEmploymentTypeCounts: (...args: unknown[]) => getEmploymentTypeCountsMock(...args),
}));

vi.mock("server-only", () => ({}));

import { EmploymentTypeModal } from "../employment-type-modal";

beforeEach(() => {
  getEmploymentTypeCountsMock.mockReset();
});

describe("EmploymentTypeModal — per-option counts (#3032)", () => {
  it("renders counts next to each option from Typesense facet", async () => {
    getEmploymentTypeCountsMock.mockResolvedValue({
      full_time: 1234,
      part_time: 56,
      contract: 78,
      internship: 9,
      temporary: 2,
      // volunteer omitted -> renders (0)
    });
    render(
      <EmploymentTypeModal
        open
        onOpenChange={() => {}}
        selected={[]}
        onToggle={() => {}}
      />,
    );

    await waitFor(() => expect(getEmploymentTypeCountsMock).toHaveBeenCalled());
    // After data resolves, each option button shows its count alongside the label.
    await waitFor(() => screen.getByText("(1234)"));
    expect(screen.getByText("(1234)")).toBeTruthy();
    expect(screen.getByText("(56)")).toBeTruthy();
    expect(screen.getByText("(78)")).toBeTruthy();
    expect(screen.getByText("(9)")).toBeTruthy();
    expect(screen.getByText("(2)")).toBeTruthy();
    // Missing key falls back to (0) for the static option.
    expect(screen.getByText("(0)")).toBeTruthy();
  });

  it("forwards filters to the action and re-fetches on filter change", async () => {
    getEmploymentTypeCountsMock.mockResolvedValue({ full_time: 10 });
    const { rerender } = render(
      <EmploymentTypeModal
        open
        onOpenChange={() => {}}
        selected={[]}
        onToggle={() => {}}
        filters={{ locationIds: [1] }}
      />,
    );
    await waitFor(() => expect(getEmploymentTypeCountsMock).toHaveBeenCalledTimes(1));
    expect(getEmploymentTypeCountsMock).toHaveBeenLastCalledWith({ locationIds: [1] });

    // Change filters → re-fetch
    rerender(
      <EmploymentTypeModal
        open
        onOpenChange={() => {}}
        selected={[]}
        onToggle={() => {}}
        filters={{ locationIds: [2] }}
      />,
    );
    await waitFor(() => expect(getEmploymentTypeCountsMock).toHaveBeenCalledTimes(2));
    expect(getEmploymentTypeCountsMock).toHaveBeenLastCalledWith({ locationIds: [2] });
  });

  it("does not fetch when the modal is closed", async () => {
    getEmploymentTypeCountsMock.mockResolvedValue({});
    render(
      <EmploymentTypeModal
        open={false}
        onOpenChange={() => {}}
        selected={[]}
        onToggle={() => {}}
      />,
    );
    // Give the effect a tick — it should bail out when `open` is false.
    await new Promise((r) => setTimeout(r, 0));
    expect(getEmploymentTypeCountsMock).not.toHaveBeenCalled();
  });
});
