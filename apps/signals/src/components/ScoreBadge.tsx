export default function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 8 ? { bg: "#dcfce7", text: "#15803d", border: "#bbf7d0" }
    : score >= 6 ? { bg: "#fef9c3", text: "#a16207", border: "#fef08a" }
    : { bg: "#fee2e2", text: "#991b1b", border: "#fecaca" };

  return (
    <span
      style={{
        background: color.bg,
        color: color.text,
        border: `1px solid ${color.border}`,
        fontSize: 11,
        fontWeight: 700,
        padding: "2px 7px",
        borderRadius: 99,
        fontVariantNumeric: "tabular-nums",
        whiteSpace: "nowrap",
      }}
    >
      {score.toFixed(1)}
    </span>
  );
}
