import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";

const saveClickMock = vi.fn();

vi.mock("@/components/TrackingDot", () => ({
  TrackingDot: () => <span data-testid="tracking-dot" />,
}));

vi.mock("@/components/PendingJobWarning", () => ({
  PendingJobIcon: () => <span data-testid="pending-job-icon" />,
}));

vi.mock("@/components/search/save-button", () => ({
  SaveButton: ({ postingId }: { postingId: string }) => (
    <button
      type="button"
      aria-label={`Save job ${postingId}`}
      onClick={() => saveClickMock(postingId)}
    >
      Save
    </button>
  ),
}));

vi.mock("@/lib/time", () => ({
  timeAgoShort: () => "1d",
}));

import { CompanyPostingRow } from "../company-posting-row";
import type { SearchResultPosting } from "@/lib/search";

const posting = {
  id: "posting-1",
  title: "Senior Engineer",
  firstSeenAt: "2026-07-22T00:00:00Z",
  relevanceScore: 1,
  locations: [{ name: "Zurich", type: "location", geoType: "city" }],
  isActive: true,
} as SearchResultPosting;

describe("CompanyPostingRow keyboard actions (issue #3166)", () => {
  beforeEach(() => {
    saveClickMock.mockClear();
  });

  it("renders Open and Save as sibling buttons without nested interactive roles", () => {
    const { container } = render(
      <CompanyPostingRow
        posting={posting}
        selected={false}
        uiLocale="en"
        onOpen={vi.fn()}
      />,
    );

    expect(container.querySelector('[role="button"]')).toBeNull();
    for (const button of container.querySelectorAll("button")) {
      expect(button.querySelector('button, [role="button"]')).toBeNull();
    }
    expect(screen.getByRole("button", { name: "Senior Engineer" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Save job posting-1" })).toBeTruthy();
  });

  it("Enter on Save does not open the posting", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(
      <CompanyPostingRow
        posting={posting}
        selected={false}
        uiLocale="en"
        onOpen={onOpen}
      />,
    );

    screen.getByRole("button", { name: "Save job posting-1" }).focus();
    await user.keyboard("{Enter}");

    expect(saveClickMock).toHaveBeenCalledOnce();
    expect(saveClickMock).toHaveBeenCalledWith("posting-1");
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("Enter on Open still opens the posting without saving", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(
      <CompanyPostingRow
        posting={posting}
        selected={false}
        uiLocale="en"
        onOpen={onOpen}
      />,
    );

    screen.getByRole("button", { name: "Senior Engineer" }).focus();
    await user.keyboard("{Enter}");

    expect(onOpen).toHaveBeenCalledOnce();
    expect(onOpen).toHaveBeenCalledWith("posting-1");
    expect(saveClickMock).not.toHaveBeenCalled();
  });
});
