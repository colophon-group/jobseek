"use client";

import { useState, useEffect } from "react";
import { ResponsiveSankey } from "@nivo/sankey";
import { useTheme } from "next-themes";
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

function getColor(id: string): string {
  if (id === "Saved") return "#a8a29e";
  if (id === "Applied") return "#38bdf8";
  if (id === "Offered") return "#34d399";
  if (id === "Not applied") return "#d6d3d1";
  if (id.startsWith("Round")) return "#fbbf24";
  if (id.startsWith("Rejected")) return "#fb7185";
  if (id.startsWith("No response")) return "#d6d3d1";
  return "#a8a29e";
}

export function SankeyFunnel({ data }: { data: FunnelData }) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  if (data.saved === 0) return null;

  const nodeSet = new Set<string>();
  const nodes: { id: string }[] = [];
  const links: { source: string; target: string; value: number }[] = [];

  function addNode(id: string) {
    if (nodeSet.has(id)) return;
    nodeSet.add(id);
    nodes.push({ id });
  }

  function addLink(source: string, target: string, value: number) {
    if (value <= 0) return;
    addNode(source);
    addNode(target);
    links.push({ source, target, value });
  }

  // Column 1: Saved
  addNode("Saved");

  // Saved → Applied / Not applied / Rejected
  addLink("Saved", "Applied", data.applied);
  addLink("Saved", "Not applied", data.noResponseAtSaved);
  addLink("Saved", "Rejected (saved)", data.rejectedAtSaved);

  // Applied → Round 1 / No response / Rejected
  if (data.interviewRounds.length > 0) {
    addLink("Applied", "Round 1", data.interviewRounds[0].count);
  }
  addLink("Applied", "No response (applied)", data.noResponseAtApplied);
  addLink("Applied", "Rejected (applied)", data.rejectedAtApplied);

  // Round N → Round N+1, with branches
  for (let i = 0; i < data.interviewRounds.length; i++) {
    const round = data.interviewRounds[i];
    const roundId = `Round ${round.round}`;

    const rejHere = data.rejectedAtRound.find((r) => r.round === round.round)?.count ?? 0;
    const nrHere = data.noResponseAtRound.find((r) => r.round === round.round)?.count ?? 0;
    const nextRound = data.interviewRounds[i + 1];

    // How many offered from this exact round (max_round = this round AND offered)
    // We don't track this per-round, so offers come from the last round only
    // Actually — a job offered at round 2 has max_round=2 and offered_at set
    // The data model gives us offered as a total, not per-round.
    // We'll link offers from the last round that has any jobs progressing to offer.

    addLink(roundId, `Rejected (round ${round.round})`, rejHere);
    addLink(roundId, `No response (round ${round.round})`, nrHere);

    const offHere = data.offeredAtRound.find((r) => r.round === round.round)?.count ?? 0;
    if (offHere > 0) {
      addLink(roundId, "Offered", offHere);
    }

    if (nextRound) {
      addLink(roundId, `Round ${nextRound.round}`, nextRound.count);
    }
  }

  // Offers without interviews (direct from Applied)
  if (data.offeredWithoutInterview > 0) {
    addLink("Applied", "Offered", data.offeredWithoutInterview);
  }

  if (links.length === 0) return null;

  const isSmall = useIsSmallScreen();
  const layout = isSmall ? "vertical" : "horizontal";
  const height = isSmall
    ? Math.max(400, 250 + nodes.length * 20)
    : Math.max(300, 200 + nodes.length * 16);
  const margin = isSmall
    ? { top: 0, right: 20, bottom: 120, left: 20 }
    : { top: 20, right: 180, bottom: 20, left: 10 };

  return (
    <div style={{ height }}>
      <ResponsiveSankey
        data={{ nodes, links }}
        layout={layout}
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
        label={(node) => `${node.id}`}
        labelPosition="outside"
        labelOrientation={isSmall ? "vertical" : "horizontal"}
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
  );
}
