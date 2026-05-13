"use client";

import { useMemo, useState, useRef } from "react";
import { useLingui } from "@lingui/react/macro";
import type { ActivityDay } from "@/lib/actions/my-jobs-stats";

// Renders a YYYY-MM-DD key from a JS Date using the *browser's* TZ
// (Date.getFullYear/Month/Date are local-TZ accessors). The server
// side now buckets `saved_at` in the same IANA TZ passed by the
// caller (see `getStats({ tz })`), so cell keys and data keys agree
// even at the day boundary in the viewer's local time. See #3199.
function formatLocal(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const WEEKS = 52;
const DAYS = 7;
const CELL = 10;
const GAP = 2;
const STEP = CELL + GAP;

const LEVELS = [
  "var(--color-border-soft, #e7e5e4)",
  "#9be9a8",
  "#40c463",
  "#30a14e",
  "#216e39",
];

const LEVELS_DARK = [
  "var(--color-border-soft, #44403c)",
  "#0e4429",
  "#006d32",
  "#26a641",
  "#39d353",
];

function getLevel(count: number): number {
  if (count === 0) return 0;
  if (count === 1) return 1;
  if (count <= 3) return 2;
  if (count <= 6) return 3;
  return 4;
}

function useDayLabels(): string[] {
  const { t } = useLingui();
  return [
    "",
    t({ id: "myJobs.heatmap.day.mon", comment: "Short Monday label for heatmap", message: "Mon" }),
    "",
    t({ id: "myJobs.heatmap.day.wed", comment: "Short Wednesday label for heatmap", message: "Wed" }),
    "",
    t({ id: "myJobs.heatmap.day.fri", comment: "Short Friday label for heatmap", message: "Fri" }),
    "",
  ];
}

function useMonthLabels(): string[] {
  const { t } = useLingui();
  return [
    t({ id: "myJobs.heatmap.month.jan", comment: "Short January label", message: "Jan" }),
    t({ id: "myJobs.heatmap.month.feb", comment: "Short February label", message: "Feb" }),
    t({ id: "myJobs.heatmap.month.mar", comment: "Short March label", message: "Mar" }),
    t({ id: "myJobs.heatmap.month.apr", comment: "Short April label", message: "Apr" }),
    t({ id: "myJobs.heatmap.month.may", comment: "Short May label", message: "May" }),
    t({ id: "myJobs.heatmap.month.jun", comment: "Short June label", message: "Jun" }),
    t({ id: "myJobs.heatmap.month.jul", comment: "Short July label", message: "Jul" }),
    t({ id: "myJobs.heatmap.month.aug", comment: "Short August label", message: "Aug" }),
    t({ id: "myJobs.heatmap.month.sep", comment: "Short September label", message: "Sep" }),
    t({ id: "myJobs.heatmap.month.oct", comment: "Short October label", message: "Oct" }),
    t({ id: "myJobs.heatmap.month.nov", comment: "Short November label", message: "Nov" }),
    t({ id: "myJobs.heatmap.month.dec", comment: "Short December label", message: "Dec" }),
  ];
}

export function ActivityHeatmap({ data }: { data: ActivityDay[] }) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const DAY_LABELS = useDayLabels();
  const MONTHS = useMonthLabels();
  const { t } = useLingui();
  const noApplications = t({ id: "myJobs.heatmap.tooltipNone", comment: "Heatmap tooltip when no applications on a date", message: "No applications on {date}" });
  const oneApplication = t({ id: "myJobs.heatmap.tooltipOne", comment: "Heatmap tooltip for 1 application", message: "1 application on {date}" });
  const multipleApplications = t({ id: "myJobs.heatmap.tooltipMultiple", comment: "Heatmap tooltip for multiple applications", message: "{count} applications on {date}" });

  const { grid, monthHeaders } = useMemo(() => {
    const map = new Map(data.map((d) => [d.date, d.count]));
    const today = new Date();
    const todayStr = formatLocal(today);

    // Start from the Sunday of (WEEKS-1) weeks ago
    const start = new Date(today);
    start.setDate(start.getDate() - today.getDay() - (WEEKS - 1) * 7);

    const columns: ({ date: string; count: number } | null)[][] = [];
    const monthHeaders: { label: string; col: number }[] = [];
    let lastMonth = -1;

    for (let w = 0; w < WEEKS; w++) {
      const col: ({ date: string; count: number } | null)[] = [];
      for (let d = 0; d < DAYS; d++) {
        const date = new Date(start);
        date.setDate(start.getDate() + w * 7 + d);
        const key = formatLocal(date);
        if (key > todayStr) {
          col.push(null);
          continue;
        }
        col.push({ date: key, count: map.get(key) ?? 0 });

        if (d === 0 && date.getMonth() !== lastMonth) {
          lastMonth = date.getMonth();
          monthHeaders.push({ label: MONTHS[lastMonth], col: w });
        }
      }
      columns.push(col);
    }

    return { grid: columns, monthHeaders };
  }, [data]);

  const labelW = 26;
  const monthH = 15;
  const gridW = WEEKS * STEP - GAP;
  const gridH = DAYS * STEP - GAP;
  const svgW = labelW + gridW;
  const svgH = monthH + gridH;

  function handleMouseEnter(e: React.MouseEvent, cell: { date: string; count: number }) {
    const rect = (e.target as SVGElement).getBoundingClientRect();
    const container = containerRef.current?.getBoundingClientRect();
    if (!container) return;
    const text = cell.count === 0
      ? noApplications.replace("{date}", cell.date)
      : cell.count === 1
        ? oneApplication.replace("{date}", cell.date)
        : multipleApplications.replace("{count}", String(cell.count)).replace("{date}", cell.date);
    setTooltip({ x: rect.left - container.left + CELL / 2, y: rect.top - container.top - 4, text });
  }

  function renderCells(levels: string[]) {
    return grid.map((col, w) =>
      col.map((cell, d) =>
        cell ? (
          // `data-date` / `data-count` mirror the cell's bucket key
          // and count. They make the heatmap inspectable from
          // DevTools and let component-level tests assert that a
          // given calendar day lights up at the right grid slot —
          // the core invariant for the #3199 TZ-alignment fix.
          <rect
            key={`${w}-${d}`}
            data-date={cell.date}
            data-count={cell.count}
            x={labelW + w * STEP}
            y={monthH + d * STEP}
            width={CELL}
            height={CELL}
            rx={2}
            fill={levels[getLevel(cell.count)]}
            onMouseEnter={(e) => handleMouseEnter(e, cell)}
            onMouseLeave={() => setTooltip(null)}
          />
        ) : null,
      ),
    );
  }

  return (
    <div ref={containerRef} className="relative">
      <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${svgW} ${svgH}`}
        width={svgW}
        height={svgH}
      >
        {/* Month labels */}
        {monthHeaders.map((m, i) => (
          <text
            key={i}
            x={labelW + m.col * STEP}
            y={11}
            className="fill-muted"
            style={{ fontSize: 9, fontFamily: "'JetBrains Mono', monospace" }}
          >
            {m.label}
          </text>
        ))}

        {/* Day labels */}
        {DAY_LABELS.map((label, i) =>
          label ? (
            <text
              key={i}
              x={0}
              y={monthH + i * STEP + CELL - 1}
              className="fill-muted"
              style={{ fontSize: 8, fontFamily: "'JetBrains Mono', monospace" }}
            >
              {label}
            </text>
          ) : null,
        )}

        {/* Light mode cells */}
        <g className="dark:hidden">{renderCells(LEVELS)}</g>

        {/* Dark mode cells */}
        <g className="hidden dark:block">{renderCells(LEVELS_DARK)}</g>
      </svg>
      </div>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="pointer-events-none absolute z-50 rounded-md border border-border-soft bg-surface px-2 py-1 text-[10px] shadow-md"
          style={{
            left: tooltip.x,
            top: tooltip.y,
            transform: "translate(-50%, -100%)",
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          {tooltip.text}
        </div>
      )}
    </div>
  );
}
