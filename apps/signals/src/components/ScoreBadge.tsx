export default function ScoreBadge({ score }: { score: number }) {
  const style =
    score >= 8
      ? { bg: "rgba(52,199,89,0.12)",   text: "#1a8c3f" }
      : score >= 6
      ? { bg: "rgba(255,159,10,0.12)",  text: "#b45309" }
      : { bg: "rgba(255,59,48,0.1)",    text: "#cc2a22" };

  return (
    <span
      style={{
        background: style.bg,
        color: style.text,
        fontSize: 11,
        fontWeight: 700,
        padding: "2px 7px",
        borderRadius: 6,
        fontVariantNumeric: "tabular-nums",
        whiteSpace: "nowrap",
      }}
    >
      {score.toFixed(1)}
    </span>
  );
}
