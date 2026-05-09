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
 * Mock occupation hierarchy mirrors production "Software Engineering"
 * domain (id=1, see #2979 follow-up — production probe in PR description):
 *   domain "Software Engineering" (id: 10)
 *     family parent "Software Engineer" (id: 100, parentId: null, domainId: 10)
 *       child "Frontend Developer" (id: 101, parentId: 100)
 *       child "Backend Developer" (id: 102, parentId: 100)
 *     family parent "DevOps Engineer" (id: 150, parentId: null, domainId: 10)
 *       child "Release Engineer" (id: 151, parentId: 150)
 *     standalone "QA Engineer" (id: 200, parentId: null, domainId: 10)
 *       — orphan-at-first-level: top-level in domain, no children
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
      {
        parent: {
          id: 150,
          slug: "devops-engineer",
          name: "DevOps Engineer",
          count: 80,
          parentId: null,
          domainId: 10,
        },
        children: [
          { id: 151, slug: "release-engineer", name: "Release Engineer", count: 20, parentId: 150, domainId: 10 },
        ],
      },
    ],
    standalone: [
      { id: 200, slug: "qa-engineer", name: "QA Engineer", count: 100, parentId: null, domainId: 10 },
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
    await waitFor(() => screen.getByText("QA Engineer"));
    // QA Engineer is a standalone in the same domain — NOT a child of Software Engineer.
    const qaButton = screen.getByText("QA Engineer").closest("button");
    expect(qaButton?.getAttribute("aria-disabled")).toBeNull();
  });
});

describe("OccupationModal — domain header toggles first-level only (#2979 follow-up)", () => {
  /**
   * User report (PR #2979 follow-up): clicking the "Software Engineering"
   * domain header was selecting the family parents (Software Engineer +
   * DevOps Engineer) AND every grandchild (Frontend, Backend, Release
   * Engineer). Expectation: select first-level only — grandchildren render
   * as disabled because their family parent is selected.
   */
  it("selects only first-level descendants on domain click", async () => {
    getAllOccupationsGroupedMock.mockResolvedValue(_groups());
    const onToggle = vi.fn();
    render(
      <OccupationModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[]}
        onToggle={onToggle}
      />,
    );
    await waitFor(() => screen.getByText("Software Engineering"));
    await userEvent.click(screen.getByText("Software Engineering"));

    // First-level: Software Engineer (100), DevOps Engineer (150),
    // QA Engineer (200) — three onToggle calls in some order.
    const ids = onToggle.mock.calls.map((call) => (call[0] as { id: number }).id).sort((a, b) => a - b);
    expect(ids).toEqual([100, 150, 200]);
    // Grandchildren MUST NOT be in the toggle list.
    expect(ids).not.toContain(101); // Frontend
    expect(ids).not.toContain(102); // Backend
    expect(ids).not.toContain(151); // Release Engineer
  });

  /** After domain click, grandchildren render disabled (parent in selection). */
  it("renders grandchildren disabled after first-level family parents are selected", async () => {
    getAllOccupationsGroupedMock.mockResolvedValue(_groups());
    render(
      <OccupationModal
        open
        onOpenChange={() => {}}
        locale="en"
        // State after domain click: first-level only.
        selected={[
          { id: 100, slug: "software-engineer", name: "Software Engineer" },
          { id: 150, slug: "devops-engineer", name: "DevOps Engineer" },
          { id: 200, slug: "qa-engineer", name: "QA Engineer" },
        ]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Frontend Developer"));

    // Grandchildren under Software Engineer
    const frontend = screen.getByText("Frontend Developer").closest("button");
    const backend = screen.getByText("Backend Developer").closest("button");
    expect(frontend?.getAttribute("aria-disabled")).toBe("true");
    expect(backend?.getAttribute("aria-disabled")).toBe("true");

    // Grandchild under DevOps Engineer
    const release = screen.getByText("Release Engineer").closest("button");
    expect(release?.getAttribute("aria-disabled")).toBe("true");
  });

  /** QA Engineer is a top-level orphan — must be selected on domain click. */
  it("selects orphan-at-first-level (QA Engineer) on domain click", async () => {
    getAllOccupationsGroupedMock.mockResolvedValue(_groups());
    const onToggle = vi.fn();
    render(
      <OccupationModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[]}
        onToggle={onToggle}
      />,
    );
    await waitFor(() => screen.getByText("Software Engineering"));
    await userEvent.click(screen.getByText("Software Engineering"));

    const qaCall = onToggle.mock.calls.find(
      (call) => (call[0] as { id: number }).id === 200,
    );
    expect(qaCall).toBeDefined();
    expect(qaCall?.[0]).toMatchObject({ id: 200, slug: "qa-engineer" });
  });

  /**
   * Domain header click is a toggle: when every first-level row is already
   * selected, the second click deselects them — and any grandchildren that
   * were independently selected get cleaned up too (would otherwise be
   * orphan-selected with no visible ancestor).
   */
  it("deselects first-level rows + drops orphan grandchildren when fully selected", async () => {
    getAllOccupationsGroupedMock.mockResolvedValue(_groups());
    const onToggle = vi.fn();
    render(
      <OccupationModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[
          { id: 100, slug: "software-engineer", name: "Software Engineer" },
          { id: 150, slug: "devops-engineer", name: "DevOps Engineer" },
          { id: 200, slug: "qa-engineer", name: "QA Engineer" },
          // Grandchild that snuck in independently of its parent.
          { id: 101, slug: "frontend-developer", name: "Frontend Developer" },
        ]}
        onToggle={onToggle}
      />,
    );
    await waitFor(() => screen.getByText("Software Engineering"));
    await userEvent.click(screen.getByText("Software Engineering"));

    const ids = onToggle.mock.calls.map((call) => (call[0] as { id: number }).id).sort((a, b) => a - b);
    // Expect the three first-level rows to be deselected, plus the
    // orphan grandchild (101) since its parent (100) is being dropped.
    expect(ids).toEqual([100, 101, 150, 200]);
  });
});
