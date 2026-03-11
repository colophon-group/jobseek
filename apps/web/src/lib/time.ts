const DAY_MS = 24 * 60 * 60 * 1000;

export function timeAgoShort(date: Date | string): string {
  const d = typeof date === "string" ? new Date(date) : date;
  const diffMs = Date.now() - d.getTime();
  const days = Math.floor(diffMs / DAY_MS);

  if (days >= 365) return `${Math.floor(days / 365)}y`;
  if (days >= 30) return `${Math.floor(days / 30)}mo`;
  if (days >= 7) return `${Math.floor(days / 7)}w`;
  if (days > 0) return `${days}d`;

  const hours = Math.floor(diffMs / (60 * 60 * 1000));
  if (hours > 0) return `${hours}h`;

  const minutes = Math.max(1, Math.floor(diffMs / (60 * 1000)));
  return `${minutes}m`;
}
