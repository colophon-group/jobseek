import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Lingui shim — register before imports of Lingui-aware modules.
import "@/test-utils/lingui-mock";

const getAllOccupationsGroupedMock = vi.fn();
vi.mock("@/lib/actions/taxonomy", () => ({
  getAllOccupationsGrouped: (...args: unknown[]) => getAllOccupationsGroupedMock(...args),
}));

vi.mock("server-only", () => ({}));

import { OccupationModal } from "../occupation-modal";

/**
 * Mock occupation hierarchy:
 *   domain "Software Engineering" (id: 10)
 *     family parent "Software Engineer" (id: 100, parentId: null, domainId: 10)
 *       child "Frontend Developer" (id: 101, parentId: 100)
 *       child "Backend Developer" (id: 102, parentId: 100)
 *     standalone "DevOps Engineer" (id: 200, parentId: null, domainId: 10)
 */
const _groups = () => [
  {
    domain: { id: 10, slug: "software-engineering", name: "Software Engineering", count: 500 },
    subGroups: [
      {
        parent: {
          id: 100,
          slug: "software-engineer",
          name: "Software Engineer",
          count: 50,
          parentId: null,
          domainId: 10,
        },
        children: [
          { id: 101, slug: "frontend-developer", name: "Frontend Developer", count: 200, parentId: 100, domainId: 10 },
          { id: 102, slug: "backend-developer", name: "Backend Developer", count: 250, parentId: 100, domainId: 10 },
        ],
      },
    ],
    standalone: [
      { id: 200, slug: "devops-engineer", name: "DevOps Engineer", count: 100, parentId: null, domainId: 10 },
    ],
  },
];

beforeEach(() => {
  getAllOccupationsGroupedMock.mockReset();
});

describe("OccupationModal — hierarchical disable (#2978)", () => {
  /** Selecting a family parent disables its children. */
  it("disables child pills when family parent is selected", async () => {
    getAllOccupationsGroupedMock.mockResolvedValue(_groups());
    render(
      <OccupationModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[{ id: 100, slug: "software-engineer", name: "Software Engineer" }]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Frontend Developer"));
    const frontendButton = screen.getByText("Frontend Developer").closest("button");
    const backendButton = screen.getByText("Backend Developer").closest("button");
    expect(frontendButton?.getAttribute("aria-disabled")).toBe("true");
    expect(frontendButton?.getAttribute("tabindex")).toBe("-1");
    expect(backendButton?.getAttribute("aria-disabled")).toBe("true");
  });

  /** Selecting the family parent auto-deselects redundant child pills. */
  it("auto-deselects children when family parent is committed", async () => {
    getAllOccupationsGroupedMock.mockResolvedValue(_groups());
    const onToggle = vi.fn();
    render(
      <OccupationModal
        open
        onOpenChange={() => {}}
        locale="en"
        // Pre-select Frontend Developer (a child of Software Engineer)
        selected={[{ id: 101, slug: "frontend-developer", name: "Frontend Developer" }]}
        onToggle={onToggle}
      />,
    );
    await waitFor(() => screen.getByText("Software Engineer"));
    await userEvent.click(screen.getByText("Software Engineer"));
    // First: add the parent. Second: remove the redundant child.
    expect(onToggle).toHaveBeenCalledTimes(2);
    expect(onToggle.mock.calls[0][0]).toMatchObject({ id: 100 });
    expect(onToggle.mock.calls[1][0]).toMatchObject({ id: 101 });
  });

  /** Disabled children render with "Included in <ancestorName>" tooltip. */
  it("renders disabled children with Included-in-ancestor tooltip wrapper", async () => {
    getAllOccupationsGroupedMock.mockResolvedValue(_groups());
    const { container } = render(
      <OccupationModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[{ id: 100, slug: "software-engineer", name: "Software Engineer" }]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Frontend Developer"));
    // The disabled pill renders a Radix Tooltip — content lives in a portal.
    // Smoke check: the trigger button is non-interactive and class shows opacity-50.
    const frontendButton = screen.getByText("Frontend Developer").closest("button");
    expect(frontendButton?.className).toContain("opacity-50");
    expect(frontendButton?.className).toContain("cursor-not-allowed");
    void container;
  });

  /** Standalones in the same domain are NOT disabled by selecting another domain peer. */
  it("does not disable peer standalones when an unrelated parent is selected", async () => {
    getAllOccupationsGroupedMock.mockResolvedValue(_groups());
    render(
      <OccupationModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[{ id: 100, slug: "software-engineer", name: "Software Engineer" }]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("DevOps Engineer"));
    // DevOps Engineer is a standalone in the same domain — NOT a child of Software Engineer.
    const devopsButton = screen.getByText("DevOps Engineer").closest("button");
    expect(devopsButton?.getAttribute("aria-disabled")).toBeNull();
  });
});
