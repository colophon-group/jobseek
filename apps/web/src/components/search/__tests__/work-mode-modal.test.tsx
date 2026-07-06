import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// Lingui shim — register before imports of Lingui-aware modules.
import "@/test-utils/lingui-mock";

const getWorkModeCountsMock = vi.fn();
vi.mock("@/lib/actions/taxonomy", () => ({
  getWorkModeCounts: (...args: unknown[]) => getWorkModeCountsMock(...args),
}));

vi.mock("server-only", () => ({}));

import { WorkModeModal } from "../work-mode-modal";

beforeEach(() => {
  getWorkModeCountsMock.mockReset();
});

describe("WorkModeModal — per-option counts (#3032)", () => {
  it("renders counts next to each option from Typesense facet", async () => {
    getWorkModeCountsMock.mockResolvedValue({
      onsite: 42,
      hybrid: 11,
      remote: 7,
    });
    render(
      <WorkModeModal
        open
        onOpenChange={() => {}}
        selected={[]}
        onToggle={() => {}}
      />,
    );

    await waitFor(() => expect(getWorkModeCountsMock).toHaveBeenCalled());
    await waitFor(() => screen.getByText("(42)"));
    expect(screen.getByText("(42)")).toBeTruthy();
    expect(screen.getByText("(11)")).toBeTruthy();
    expect(screen.getByText("(7)")).toBeTruthy();
  });

  it("falls back to (0) for modes missing from the facet response", async () => {
    getWorkModeCountsMock.mockResolvedValue({ remote: 99 });
    render(
      <WorkModeModal
        open
        onOpenChange={() => {}}
        selected={[]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("(99)"));
    // onsite + hybrid both render (0) — assert there are two zero-counts.
    const zeros = screen.getAllByText("(0)");
    expect(zeros.length).toBe(2);
  });

  it("re-fetches when cross-filter context changes", async () => {
    getWorkModeCountsMock.mockResolvedValue({ remote: 1 });
    const { rerender } = render(
      <WorkModeModal
        open
        onOpenChange={() => {}}
        selected={[]}
        onToggle={() => {}}
        filters={{ occupationIds: [10] }}
      />,
    );
    await waitFor(() => expect(getWorkModeCountsMock).toHaveBeenCalledTimes(1));
    expect(getWorkModeCountsMock).toHaveBeenLastCalledWith({ occupationIds: [10] });

    rerender(
      <WorkModeModal
        open
        onOpenChange={() => {}}
        selected={[]}
        onToggle={() => {}}
        filters={{ occupationIds: [10, 11] }}
      />,
    );
    await waitFor(() => expect(getWorkModeCountsMock).toHaveBeenCalledTimes(2));
    expect(getWorkModeCountsMock).toHaveBeenLastCalledWith({ occupationIds: [10, 11] });
  });

  it("does not fetch when the modal is closed", async () => {
    getWorkModeCountsMock.mockResolvedValue({});
    render(
      <WorkModeModal
        open={false}
        onOpenChange={() => {}}
        selected={[]}
        onToggle={() => {}}
      />,
    );
    await new Promise((r) => setTimeout(r, 0));
    expect(getWorkModeCountsMock).not.toHaveBeenCalled();
  });
});
