"use client";

import { useState, useEffect } from "react";
import { ResponsiveSankey } from "@nivo/sankey";
import { useTheme } from "next-themes";
import { useLingui } from "@lingui/react/macro";
import type { FunnelData } from "@/lib/actions/my-jobs-stats";

function useIsSmallScreen() {
  const [small, setSmall] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 640px)");
    setSmall(mq.matches);
    const handler = (e: MediaQueryListEvent) => setSmall(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return small;
}

const FONT = "'JetBrains Mono', 'Inter', sans-serif";

/** Color lookup keyed on the untranslated category tag we embed in each node ID */
function getColor(id: string): string {
  if (id.startsWith("__saved")) return "#a8a29e";
  if (id.startsWith("__applied")) return "#38bdf8";
  if (id.startsWith("__offered")) return "#34d399";
  if (id.startsWith("__notApplied")) return "#d6d3d1";
  if (id.startsWith("__round")) return "#fbbf24";
  if (id.startsWith("__rejected")) return "#fb7185";
  if (id.startsWith("__noResponse")) return "#d6d3d1";
  return "#a8a29e";
}

export function SankeyFunnel({ data }: { data: FunnelData }) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const { t } = useLingui();
  const isSmall = useIsSmallScreen();

  const l = {
    saved: t({ id: "myJobs.funnel.saved", comment: "Sankey funnel node: saved jobs", message: "Saved" }),
    applied: t({ id: "myJobs.funnel.applied", comment: "Sankey funnel node: applied", message: "Applied" }),
    offered: t({ id: "myJobs.funnel.offered", comment: "Sankey funnel node: offered", message: "Offered" }),
    notApplied: t({ id: "myJobs.funnel.notApplied", comment: "Sankey funnel node: not applied", message: "Not applied" }),
    rejected: t({ id: "myJobs.funnel.rejected", comment: "Sankey funnel node label prefix: rejected", message: "Rejected" }),
    noResponse: t({ id: "myJobs.funnel.noResponse", comment: "Sankey funnel node label prefix: no response", message: "No response" }),
    round: t({ id: "myJobs.funnel.round", comment: "Sankey funnel node: interview round prefix", message: "Round" }),
  };

  if (data.saved === 0) return null;

  const nodeSet = new Set<string>();
  const nodes: { id: string }[] = [];
  const links: { source: string; target: string; value: number }[] = [];
  /** Maps internal category-tagged ID → display label */
  const labelMap = new Map<string, string>();

  function addNode(id: string, label: string) {
    if (nodeSet.has(id)) return;
    nodeSet.add(id);
    nodes.push({ id });
    labelMap.set(id, label);
  }

  function addLink(source: string, target: string, value: number) {
    if (value <= 0) return;
    links.push({ source, target, value });
  }

  // Build node IDs with category prefix for color lookup, display labels translated
  const nSaved = "__saved";
  const nApplied = "__applied";
  const nOffered = "__offered";
  const nNotApplied = "__notApplied";
  const rejSaved = "__rejected_saved";
  const rejApplied = "__rejected_applied";
  const nrApplied = "__noResponse_applied";
  const roundId = (n: number) => `__round_${n}`;
  const rejRound = (n: number) => `__rejected_round_${n}`;
  const nrRound = (n: number) => `__noResponse_round_${n}`;

  // Column 1: Saved
  addNode(nSaved, l.saved);

  // Saved → Applied / Not applied / Rejected
  addNode(nApplied, l.applied);
  addLink(nSaved, nApplied, data.applied);
  addNode(nNotApplied, l.notApplied);
  addLink(nSaved, nNotApplied, data.noResponseAtSaved);
  addNode(rejSaved, `${l.rejected} (${l.saved.toLowerCase()})`);
  addLink(nSaved, rejSaved, data.rejectedAtSaved);

  // Applied → Round 1 / No response / Rejected
  if (data.interviewRounds.length > 0) {
    const r1 = roundId(1);
    addNode(r1, `${l.round} 1`);
    addLink(nApplied, r1, data.interviewRounds[0].count);
  }
  addNode(nrApplied, `${l.noResponse} (${l.applied.toLowerCase()})`);
  addLink(nApplied, nrApplied, data.noResponseAtApplied);
  addNode(rejApplied, `${l.rejected} (${l.applied.toLowerCase()})`);
  addLink(nApplied, rejApplied, data.rejectedAtApplied);

  // Round N → Round N+1, with branches
  for (let i = 0; i < data.interviewRounds.length; i++) {
    const round = data.interviewRounds[i];
    const rId = roundId(round.round);
    addNode(rId, `${l.round} ${round.round}`);

    const rejHere = data.rejectedAtRound.find((r) => r.round === round.round)?.count ?? 0;
    const nrHere = data.noResponseAtRound.find((r) => r.round === round.round)?.count ?? 0;
    const nextRound = data.interviewRounds[i + 1];

    const rejId = rejRound(round.round);
    addNode(rejId, `${l.rejected} (${l.round.toLowerCase()} ${round.round})`);
    addLink(rId, rejId, rejHere);
    const nrId = nrRound(round.round);
    addNode(nrId, `${l.noResponse} (${l.round.toLowerCase()} ${round.round})`);
    addLink(rId, nrId, nrHere);

    const offHere = data.offeredAtRound.find((r) => r.round === round.round)?.count ?? 0;
    if (offHere > 0) {
      addNode(nOffered, l.offered);
      addLink(rId, nOffered, offHere);
    }

    if (nextRound) {
      const nrId2 = roundId(nextRound.round);
      addNode(nrId2, `${l.round} ${nextRound.round}`);
      addLink(rId, nrId2, nextRound.count);
    }
  }

  // Offers without interviews (direct from Applied)
  if (data.offeredWithoutInterview > 0) {
    addNode(nOffered, l.offered);
    addLink(nApplied, nOffered, data.offeredWithoutInterview);
  }

  // Filter to only nodes that participate in links
  const usedNodeIds = new Set(links.flatMap((l) => [l.source, l.target]));
  const filteredNodes = nodes.filter((n) => usedNodeIds.has(n.id) || n.id === nSaved);

  if (links.length === 0) return null;

  const stages = [
    {
      key: "saved",
      label: l.saved,
      count: data.saved,
      outcomes: [
        { label: l.rejected, count: data.rejectedAtSaved },
        { label: l.notApplied, count: data.noResponseAtSaved },
      ],
    },
    {
      key: "applied",
      label: l.applied,
      count: data.applied,
      outcomes: [
        { label: l.rejected, count: data.rejectedAtApplied },
        { label: l.noResponse, count: data.noResponseAtApplied },
      ],
    },
    ...data.interviewRounds.map((round) => ({
      key: `round-${round.round}`,
      label: `${l.round} ${round.round}`,
      count: round.count,
      outcomes: [
        {
          label: l.rejected,
          count: data.rejectedAtRound.find((item) => item.round === round.round)?.count ?? 0,
        },
        {
          label: l.noResponse,
          count: data.noResponseAtRound.find((item) => item.round === round.round)?.count ?? 0,
        },
      ],
    })),
    {
      key: "offered",
      label: l.offered,
      count: data.offered,
      outcomes: [],
    },
  ].filter((stage) => stage.count > 0);

  const accessibleSummary = (
    <ol
      data-testid="funnel-summary"
      className={isSmall ? "space-y-2" : "sr-only"}
      aria-label={t({
        id: "myJobs.funnel.mobileLabel",
        comment: "Accessible label for the application funnel summary",
        message: "Application funnel",
      })}
    >
      {stages.map((stage, index) => {
        const outcomes = stage.outcomes.filter((outcome) => outcome.count > 0);
        return (
          <li key={stage.key} className="relative rounded-lg border border-border-soft bg-surface px-3 py-2.5">
            {index > 0 && (
              <span aria-hidden="true" className="absolute -top-2.5 left-6 h-2.5 border-l border-border-soft" />
            )}
            <div className="flex items-center justify-between gap-3">
              <span className="min-w-0 font-medium">{stage.label}</span>
              <span className="shrink-0 rounded-full bg-border-soft px-2 py-0.5 font-mono text-sm tabular-nums">
                {stage.count}
              </span>
            </div>
            {outcomes.length > 0 && (
              <ul className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
                {outcomes.map((outcome) => (
                  <li key={outcome.label}>
                    {outcome.label}: <span className="font-mono tabular-nums">{outcome.count}</span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ol>
  );

  if (isSmall) return accessibleSummary;

  const sankeyData = { nodes: filteredNodes, links };
  const height = Math.max(300, 200 + nodes.length * 16);
  const margin = { top: 20, right: 180, bottom: 20, left: 10 };

  return (
    <>
      {accessibleSummary}
      <div data-testid="sankey-visual" aria-hidden="true" style={{ height }}>
        <ResponsiveSankey
          data={sankeyData}
          layout="horizontal"
          margin={margin}
          align="start"
          colors={(node) => getColor(node.id as string)}
          nodeOpacity={1}
          nodeHoverOpacity={1}
          nodeThickness={14}
          nodeSpacing={16}
          nodeBorderWidth={0}
          nodeBorderRadius={4}
          linkOpacity={isDark ? 0.35 : 0.25}
          linkHoverOpacity={isDark ? 0.55 : 0.4}
          linkContract={1}
          linkBlendMode={isDark ? "screen" : "multiply"}
          enableLinkGradient={false}
          label={(node) => labelMap.get(node.id as string) ?? (node.id as string)}
          labelPosition="outside"
          labelOrientation="horizontal"
          labelPadding={12}
          labelTextColor={isDark ? "#e7e5e4" : "#1c1917"}
          theme={{
            text: {
              fontFamily: FONT,
              fontSize: 11,
            },
            tooltip: {
              container: {
                fontFamily: FONT,
                background: isDark ? "#292524" : "#ffffff",
                color: isDark ? "#e7e5e4" : "#1c1917",
                fontSize: 12,
                borderRadius: 6,
                boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
              },
            },
          }}
        />
      </div>
    </>
  );
}
