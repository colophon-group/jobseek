import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { readFileSync } from "node:fs";
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

const linguiState = vi.hoisted(() => ({ locale: "en" }));
const translateDescriptor = vi.hoisted(() => (
  {
    format({
      id,
      message,
      values = {},
    }: {
      id?: string;
      message?: string;
      values?: Record<string, unknown>;
    }) {
      if (id === "myJobs.heatmap.label") {
        return linguiState.locale === "de"
          ? "Bewerbungsaktivität"
          : "Application activity";
      }
      if (id === "myJobs.heatmap.emptySummary") {
        return linguiState.locale === "de"
          ? "Keine Bewerbungsaktivität im angezeigten Jahr."
          : "No application activity in the displayed year.";
      }
      if (id === "myJobs.heatmap.tooltip") {
        const count = Number(values.count ?? 0);
        const date = String(values.date ?? "");
        if (linguiState.locale === "de") {
          if (count === 0) return `Keine Bewerbungen am ${date}`;
          if (count === 1) return `1 Bewerbung am ${date}`;
          return `${count} Bewerbungen am ${date}`;
        }
        if (count === 0) return `No applications on ${date}`;
        if (count === 1) return `1 application on ${date}`;
        return `${count} applications on ${date}`;
      }
      return (message ?? "").replace(/\{(\w+)\}/g, (_, key: string) => String(values[key] ?? ""));
    },
  }
));

// Lingui hook is required by the component for label rendering; stub it.
vi.mock("@lingui/react/macro", () => ({
  useLingui: () => ({
    i18n: { locale: linguiState.locale, _: translateDescriptor.format },
    t: translateDescriptor.format,
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
    linguiState.locale = "en";
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

describe("ActivityHeatmap — locale labels and plural tooltip (#3150)", () => {
  beforeEach(() => {
    linguiState.locale = "en";
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("uses a single ICU tooltip key and browser Intl labels instead of catalog-backed month/day keys", () => {
    const source = readFileSync("src/components/my-jobs/activity-heatmap.tsx", "utf8");

    expect(source).toContain('id: "myJobs.heatmap.tooltip"');
    expect(source).toContain("{count, plural, =0");
    expect(source).toContain("Intl.DateTimeFormat");
    expect(source).not.toContain("myJobs.heatmap.tooltipNone");
    expect(source).not.toContain("myJobs.heatmap.tooltipOne");
    expect(source).not.toContain("myJobs.heatmap.tooltipMultiple");
    expect(source).not.toContain("myJobs.heatmap.month.");
    expect(source).not.toContain("myJobs.heatmap.day.");
  });

  it("renders weekday and month labels from the active locale", () => {
    linguiState.locale = "de";
    vi.setSystemTime(new Date(2026, 6, 15, 12, 0, 0, 0));

    const { container } = render(<ActivityHeatmap data={[]} />);
    const labels = Array.from(container.querySelectorAll("text"))
      .map((el) => el.textContent)
      .filter(Boolean);

    expect(labels).toContain(new Intl.DateTimeFormat("de", { weekday: "short" }).format(new Date(2026, 0, 5)));
    expect(labels).toContain(new Intl.DateTimeFormat("de", { weekday: "short" }).format(new Date(2026, 0, 7)));
    expect(labels).toContain(new Intl.DateTimeFormat("de", { weekday: "short" }).format(new Date(2026, 0, 9)));
    expect(labels).toContain(new Intl.DateTimeFormat("de", { month: "short" }).format(new Date(2026, 7, 1)));
  });

  it("omits a partial first-month label when it would collide with the next month", () => {
    vi.setSystemTime(new Date(2026, 6, 22, 12, 0, 0, 0));

    const { container } = render(<ActivityHeatmap data={[]} />);
    const monthLabels = Array.from(
      container.querySelectorAll<SVGTextElement>("text[data-month-column]"),
    );

    expect(monthLabels[0]?.textContent).toBe(
      new Intl.DateTimeFormat("en", { month: "short" }).format(new Date(2026, 7, 1)),
    );
    const columns = monthLabels.map((label) => Number(label.dataset.monthColumn));
    for (let index = 1; index < columns.length; index++) {
      expect(columns[index] - columns[index - 1]).toBeGreaterThanOrEqual(3);
    }
  });

  const tooltipCases: Array<[number, string]> = [
    [0, "No applications"],
    [1, "1 application"],
    [4, "4 applications"],
  ];

  it.each(tooltipCases)("renders tooltip plural branch for count %i", (count, prefix) => {
    const now = new Date();
    now.setHours(12, 0, 0, 0);
    vi.setSystemTime(now);

    const todayKey = formatLocal(now);
    const { container } = render(
      <ActivityHeatmap data={count > 0 ? [{ date: todayKey, count }] : []} />,
    );

    const todayCell = findCellByDate(container, todayKey);
    expect(todayCell).toBeDefined();
    fireEvent.mouseEnter(todayCell!);

    const localizedDate = new Intl.DateTimeFormat("en", {
      dateStyle: "long",
    }).format(now);
    expect(container.textContent).toContain(`${prefix} on ${localizedDate}`);
  });
});

describe("ActivityHeatmap — accessible summary (#6024)", () => {
  beforeEach(() => {
    linguiState.locale = "en";
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("exposes every non-zero displayed day and hides decorative SVG cells", () => {
    const now = new Date(2026, 6, 22, 12, 0, 0, 0);
    vi.setSystemTime(now);
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);

    const { container, getByRole, getByTestId } = render(
      <ActivityHeatmap
        data={[
          { date: formatLocal(yesterday), count: 2 },
          { date: formatLocal(now), count: 1 },
        ]}
      />,
    );

    const region = getByRole("region", { name: "Application activity" });
    const summary = getByTestId("heatmap-summary");
    expect(region.contains(summary)).toBe(true);
    expect(summary.textContent).toContain(
      `2 applications on ${new Intl.DateTimeFormat("en", { dateStyle: "long" }).format(yesterday)}`,
    );
    expect(summary.textContent).toContain(
      `1 application on ${new Intl.DateTimeFormat("en", { dateStyle: "long" }).format(now)}`,
    );
    expect(summary.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("svg")?.closest('[aria-hidden="true"]')).not.toBeNull();
  });

  it("exposes a localized empty summary", () => {
    linguiState.locale = "de";
    vi.setSystemTime(new Date(2026, 6, 22, 12, 0, 0, 0));

    const { getByRole, getByTestId } = render(<ActivityHeatmap data={[]} />);

    expect(getByRole("region", { name: "Bewerbungsaktivität" })).toBeTruthy();
    expect(getByTestId("heatmap-summary").textContent).toBe(
      "Keine Bewerbungsaktivität im angezeigten Jahr.",
    );
  });

  it("formats non-English activity dates with the active locale", () => {
    linguiState.locale = "de";
    const now = new Date(2026, 6, 22, 12, 0, 0, 0);
    vi.setSystemTime(now);

    const { getByTestId } = render(
      <ActivityHeatmap data={[{ date: formatLocal(now), count: 1 }]} />,
    );

    const localizedDate = new Intl.DateTimeFormat("de", {
      dateStyle: "long",
    }).format(now);
    expect(getByTestId("heatmap-summary").textContent).toContain(
      `1 Bewerbung am ${localizedDate}`,
    );
  });
});
