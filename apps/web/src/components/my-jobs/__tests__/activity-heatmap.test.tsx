import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { ActivityHeatmap } from "../activity-heatmap";

/**
 * #3199 — Activity-heatmap grid alignment.
 *
 * The fix moves day bucketing on the server to `AT TIME ZONE <viewer-tz>`
 * so the date keys the server returns match the keys the client builds
 * (which use browser-TZ `Date.getFullYear/Month/Date`).
 *
 * The contract these tests pin:
 *
 *   - A data row with `{ date: "<today-YYYY-MM-DD>", count: N }` lights
 *     up the cell at the *today* slot of the grid, not yesterday and
 *     not tomorrow — for any browser TZ.
 *   - A data row whose date is one day before today lights up the cell
 *     immediately before today.
 *   - A data row whose date is the server-UTC day but NOT the
 *     browser-local day (the pre-fix shape) misses today's cell. The
 *     calibration test below reproduces that bug shape against the
 *     same component to prove the alignment matters.
 */

// Lingui hook is required by the component for label rendering; stub it.
vi.mock("@lingui/react/macro", () => ({
  useLingui: () => ({
    t: ({ message }: { message?: string }) => message ?? "",
  }),
}));

function formatLocal(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function getCells(container: HTMLElement): HTMLElement[] {
  // The component renders two `<g>` groups (light + dark mode). Both
  // contain the same data — pick either by querying any rect with a
  // data-date attribute.
  return Array.from(
    container.querySelectorAll<HTMLElement>("rect[data-date]"),
  );
}

function findCellByDate(
  container: HTMLElement,
  date: string,
): HTMLElement | undefined {
  return getCells(container).find((el) => el.getAttribute("data-date") === date);
}

describe("ActivityHeatmap — TZ-aligned cell rendering (#3199)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders a non-zero cell for today when data uses today's local date", () => {
    // Pin "now" to 23:30 in the host's local TZ. The exact TZ does not
    // matter here — what matters is that `today` (formatLocal(new Date()))
    // matches the data key.
    const now = new Date();
    now.setHours(23, 30, 0, 0);
    vi.setSystemTime(now);

    const todayKey = formatLocal(now);
    const { container } = render(
      <ActivityHeatmap data={[{ date: todayKey, count: 1 }]} />,
    );

    const todayCell = findCellByDate(container, todayKey);
    expect(todayCell, `expected a cell for ${todayKey}`).toBeDefined();
    // Both light + dark groups exist, so the count appears on both.
    const cells = getCells(container).filter(
      (el) => el.getAttribute("data-date") === todayKey,
    );
    expect(cells.length).toBe(2);
    for (const cell of cells) {
      expect(cell.getAttribute("data-count")).toBe("1");
    }
  });

  it("does NOT light up today when data uses a different (e.g. UTC-shifted) date — calibration", () => {
    // This mirrors the pre-fix bug: server returns the UTC-bucketed
    // day, which may be tomorrow relative to the user's local time.
    // The component (using browser-local Date) never finds a match for
    // that key as "today", so today's cell stays at count=0.
    const now = new Date();
    now.setHours(23, 30, 0, 0);
    vi.setSystemTime(now);

    const todayKey = formatLocal(now);
    const tomorrow = new Date(now);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const tomorrowKey = formatLocal(tomorrow);

    const { container } = render(
      <ActivityHeatmap data={[{ date: tomorrowKey, count: 1 }]} />,
    );

    // Today's cell is rendered (it's within the 52-week window) but
    // its count is 0 — the dot is misplaced.
    const todayCell = findCellByDate(container, todayKey);
    expect(todayCell, `expected a cell for ${todayKey}`).toBeDefined();
    expect(todayCell!.getAttribute("data-count")).toBe("0");

    // And the cell at tomorrowKey doesn't exist in the grid: future
    // days are filtered out (col.push(null)).
    const tomorrowCell = findCellByDate(container, tomorrowKey);
    expect(tomorrowCell).toBeUndefined();
  });

  it("lights up yesterday's cell when data uses yesterday's local date", () => {
    const now = new Date();
    now.setHours(12, 0, 0, 0);
    vi.setSystemTime(now);

    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    const yKey = formatLocal(yesterday);

    const { container } = render(
      <ActivityHeatmap data={[{ date: yKey, count: 4 }]} />,
    );

    const cell = findCellByDate(container, yKey);
    expect(cell).toBeDefined();
    expect(cell!.getAttribute("data-count")).toBe("4");

    // Today's cell exists and has count=0.
    const today = findCellByDate(container, formatLocal(now));
    expect(today).toBeDefined();
    expect(today!.getAttribute("data-count")).toBe("0");
  });

  it("renders an empty grid (all zero) for empty data", () => {
    const { container } = render(<ActivityHeatmap data={[]} />);
    const cells = getCells(container);
    expect(cells.length).toBeGreaterThan(0);
    for (const cell of cells) {
      expect(cell.getAttribute("data-count")).toBe("0");
    }
  });
});
