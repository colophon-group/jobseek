"use server";

import { sql } from "drizzle-orm";
import { db } from "@/db";
import { getSessionUserId } from "@/lib/sessionCache";

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

  const from = params?.from ?? null;
  const to = params?.to ?? null;
  const dateFilter = from && to
    ? sql`AND sj.saved_at >= ${from}::timestamptz AND sj.saved_at < (${to}::date + 1)::timestamptz`
    : from
      ? sql`AND sj.saved_at >= ${from}::timestamptz`
      : to
        ? sql`AND sj.saved_at < (${to}::date + 1)::timestamptz`
        : sql``;

  // Run funnel + activity queries in parallel (independent aggregations)
  const [rows, activityRows] = await Promise.all([
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
    db.execute<{
      [key: string]: unknown;
      day: string;
      cnt: number;
    }>(sql`
      SELECT to_char(saved_at, 'YYYY-MM-DD') AS day, count(*)::int AS cnt
      FROM saved_job
      WHERE user_id = ${userId}
        AND saved_at >= now() - interval '52 weeks'
      GROUP BY day
      ORDER BY day
    `),
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
