export function scoreColor(score: number | null): string {
  if (score === null) return "bg-gray-100";
  if (score >= 0.8) return "bg-green-100";
  if (score >= 0.6) return "bg-blue-100";
  if (score >= 0.4) return "bg-yellow-100";
  return "bg-red-100";
}

export function formatScore(score: number | null): string {
  if (score === null) return "—";
  return `${Math.round(score * 100)}%`;
}
