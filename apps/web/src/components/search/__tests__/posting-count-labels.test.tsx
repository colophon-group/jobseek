import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import "@/test-utils/lingui-mock";
import { ActivePostingCount, YearPostingCount } from "../posting-count-labels";

function renderCounts(active: number, year: number) {
  const { container } = render(
    <p>
      <ActivePostingCount count={active} />
      {" · "}
      <YearPostingCount count={year} />
    </p>,
  );
  return container.textContent;
}

describe("posting count labels", () => {
  it("selects the singular fallback", () => {
    expect(renderCounts(1, 1)).toBe("1 active job · 1 in the last year");
  });

  it("selects the plural fallback", () => {
    expect(renderCounts(3333, 8698)).toBe("3333 active jobs · 8698 in the last year");
  });
});
