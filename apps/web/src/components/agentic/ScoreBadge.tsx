export default function ScoreBadge({ score }: { score: number }) {
  const cls =
    score >= 8
      ? "bg-success-bg text-success"
      : score >= 6
      ? "bg-warning-bg text-warning"
      : "bg-error-bg text-error";

  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-bold tabular-nums whitespace-nowrap ${cls}`}>
      {score.toFixed(1)}
    </span>
  );
}
