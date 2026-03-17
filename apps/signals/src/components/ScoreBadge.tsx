export default function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 8 ? "#4ade80"
    : score >= 6 ? "#facc15"
    : "#f87171";

  return (
    <span
      style={{
        color,
        fontSize: 12,
        fontWeight: 700,
        fontVariantNumeric: "tabular-nums",
        minWidth: 24,
        display: "inline-block",
      }}
    >
      {score.toFixed(1)}
    </span>
  );
}
