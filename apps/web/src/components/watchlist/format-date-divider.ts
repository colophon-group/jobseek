/**
 * Date-divider helpers extracted from `watchlist-job-list.tsx` so they
 * can be unit-tested without dragging the entire React/Lingui surface
 * into the test runner.
 *
 * `formatDateDivider` MUST receive an explicit `locale` — see #3221.
 * Calling `Date#toLocaleDateString(undefined, ...)` picks the runtime
 * default (en-US on Node SSR, browser locale on the client) and
 * produces a hydration mismatch for non-English viewers.
 */

export function formatDateDivider(
  dateStr: string,
  todayLabel: string,
  yesterdayLabel: string,
  locale: string,
): string {
  const date = new Date(dateStr);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);

  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate());

  if (d.getTime() === today.getTime()) return todayLabel;
  if (d.getTime() === yesterday.getTime()) return yesterdayLabel;

  // Explicit `locale` (passed in by the page via the `[lang]` route
  // param) — `undefined` would pick the runtime default, which is
  // en-US on the Node SSR side and the browser locale on the client.
  // That mismatch produces "Wed, May 13" server-side and "Mi., 13. Mai"
  // client-side for a German viewer, causing a hydration mismatch. See
  // #3221.
  return d.toLocaleDateString(locale, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

export function getDateKey(dateStr: string): string {
  const d = new Date(dateStr);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
