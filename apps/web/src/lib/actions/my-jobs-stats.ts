"use server";

import { sql } from "drizzle-orm";
import { db } from "@/db";
import { withDbRetry } from "@/lib/db-retry";
import { getSessionUserId } from "@/lib/sessionCache";

// IANA time-zone names are limited to ASCII letters, digits, '+', '-',
// '_', and '/'. The pattern intentionally rejects anything else (e.g.
// SQL meta-characters, whitespace, quotes) before the value is handed
// to Postgres. We still pass the value through a parameter via the
// drizzle `sql` tagged template, but validating up-front lets the
// fallback to UTC kick in cleanly when a bad / malformed value arrives.
// Length is also bounded — the longest real IANA name today
// ("America/Argentina/ComodRivadavia") is 32 chars, so 64 is generous.
const IANA_TZ_RE = /^[A-Za-z][A-Za-z0-9_+\-/]{0,63}$/;

function normalizeTz(tz: string | undefined | null): string {
  if (!tz) return "UTC";
  return IANA_TZ_RE.test(tz) ? tz : "UTC";
}

export interface FunnelData {
  saved: number;
  applied: number;
  offered: number;
  offeredWithoutInterview: number;
  rejectedAtSaved: number;
  rejectedAtApplied: number;
  noResponseAtSaved: number;
  noResponseAtApplied: number;
  interviewRounds: { round: number; count: number }[];
  rejectedAtRound: { round: number; count: number }[];
  noResponseAtRound: { round: number; count: number }[];
  offeredAtRound: { round: number; count: number }[];
}

export interface ActivityDay {
  date: string; // YYYY-MM-DD
  count: number;
}

export interface StatsData {
  funnel: FunnelData;
  activity: ActivityDay[];
  activityTotal: number;
}

export async function getStats(params?: {
  from?: string;
  to?: string;
  /**
   * IANA timezone name from the viewer's browser (e.g.
   * "America/New_York"). Used to bucket activity-heatmap days and to
   * interpret the date-range filter at the viewer's local midnight
   * rather than UTC. Falls back to "UTC" when missing or malformed —
   * preserving the pre-#3199 behaviour for older clients. See #3199.
   */
  tz?: string;
}): Promise<StatsData> {
  const userId = await getSessionUserId();
  if (!userId) {
    return {
      funnel: {
        saved: 0, applied: 0, offered: 0, offeredWithoutInterview: 0,
        rejectedAtSaved: 0, rejectedAtApplied: 0,
        noResponseAtSaved: 0, noResponseAtApplied: 0,
        interviewRounds: [], rejectedAtRound: [], noResponseAtRound: [], offeredAtRound: [],
      },
      activity: [],
      activityTotal: 0,
    };
  }

  const tz = normalizeTz(params?.tz);
  const from = params?.from ?? null;
  const to = params?.to ?? null;
  // Date-range filter: interpret `from`/`to` as calendar days in the
  // viewer's local TZ, not in Postgres's TZ. Otherwise a NYC user
  // picking "2026-05-14" would silently mean UTC midnight, missing
  // anything saved between 19:00 and midnight local on May 13.
  const dateFilter = from && to
    ? sql`AND sj.saved_at >= (${from}::timestamp AT TIME ZONE ${tz}) AND sj.saved_at < ((${to}::date + 1)::timestamp AT TIME ZONE ${tz})`
    : from
      ? sql`AND sj.saved_at >= (${from}::timestamp AT TIME ZONE ${tz})`
      : to
        ? sql`AND sj.saved_at < ((${to}::date + 1)::timestamp AT TIME ZONE ${tz})`
        : sql``;

  // Run funnel + activity queries in parallel (independent aggregations)
  const [rows, activityRows] = await Promise.all([
    withDbRetry(
      () =>
        db.execute<{
          [key: string]: unknown;
          status: string;
          interview_count: number;
          max_round: number;
        }>(sql`
          SELECT
            sj.status,
            coalesce(ic.cnt, 0)::int AS interview_count,
            coalesce(ic.max_round, 0)::int AS max_round
          FROM saved_job sj
          LEFT JOIN (
            SELECT saved_job_id, count(*) AS cnt, max(round) AS max_round
            FROM application_interview
            GROUP BY saved_job_id
          ) ic ON ic.saved_job_id = sj.id
          WHERE sj.user_id = ${userId} ${dateFilter}
        `),
      { label: "myJobsStats.funnel" },
    ),
    withDbRetry(
      () =>
        // Bucket `saved_at` (timestamptz, stored in UTC) into the
        // viewer's local calendar day. Without `AT TIME ZONE` Postgres
        // formats in its own TZ (UTC on Supabase), so a NYC user
        // saving at 23:00 local lands on tomorrow's bucket while the
        // client's heatmap grid is computed in browser TZ — the dot
        // disappears or moves a cell. See #3199.
        db.execute<{
          [key: string]: unknown;
          day: string;
          cnt: number;
        }>(sql`
          SELECT to_char(saved_at AT TIME ZONE ${tz}, 'YYYY-MM-DD') AS day, count(*)::int AS cnt
          FROM saved_job
          WHERE user_id = ${userId}
            AND saved_at >= now() - interval '52 weeks'
          GROUP BY day
          ORDER BY day
        `),
      { label: "myJobsStats.activity" },
    ),
  ]);

  type Row = { status: string; interview_count: number; max_round: number };
  const all = rows as unknown as Row[];

  const total = all.length;
  const applied = all.filter((r) => r.status !== "saved");
  const withInterviews = applied.filter((r) => r.interview_count > 0);

  const noResponseAtSaved = all.filter((r) => r.status === "saved").length;
  const rejectedAtSaved = 0;

  const appliedNoInterview = applied.filter((r) => r.interview_count === 0);
  const rejectedAtApplied = appliedNoInterview.filter((r) => r.status === "rejected").length;
  const offeredWithoutInterview = appliedNoInterview.filter((r) => r.status === "offered").length;
  const noResponseAtApplied = appliedNoInterview.filter((r) => r.status === "applied").length;

  const maxRoundSeen = withInterviews.reduce((m, r) => Math.max(m, r.max_round), 0);
  const interviewRounds: { round: number; count: number }[] = [];
  const rejectedAtRound: { round: number; count: number }[] = [];
  const noResponseAtRound: { round: number; count: number }[] = [];
  const offeredAtRound: { round: number; count: number }[] = [];

  for (let round = 1; round <= maxRoundSeen; round++) {
    const atRound = withInterviews.filter((r) => r.max_round >= round);
    interviewRounds.push({ round, count: atRound.length });

    const atExactRound = withInterviews.filter((r) => r.max_round === round);
    const rej = atExactRound.filter((r) => r.status === "rejected").length;
    if (rej > 0) rejectedAtRound.push({ round, count: rej });

    const off = atExactRound.filter((r) => r.status === "offered").length;
    if (off > 0) offeredAtRound.push({ round, count: off });

    const nr = atExactRound.filter((r) => r.status === "interviewing" || r.status === "applied").length;
    if (nr > 0) noResponseAtRound.push({ round, count: nr });
  }

  const activity = (activityRows as unknown as { day: string; cnt: number }[]).map((r) => ({
    date: r.day,
    count: r.cnt,
  }));
  const activityTotal = activity.reduce((s, d) => s + d.count, 0);

  return {
    funnel: {
      saved: total,
      applied: applied.length,
      offered: all.filter((r) => r.status === "offered").length,
      offeredWithoutInterview,
      rejectedAtSaved,
      rejectedAtApplied,
      noResponseAtSaved,
      noResponseAtApplied,
      interviewRounds,
      rejectedAtRound,
      noResponseAtRound,
      offeredAtRound,
    },
    activity,
    activityTotal,
  };
}
