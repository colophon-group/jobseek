import type { DayResult, JobRecord, TimelinePoint } from './types.js';
import { daysBetween } from './inventory.js';

/**
 * Score a job posting's "ghost likelihood" based on how long it has been posted.
 *
 * 0–25   → Normal (fresh or actively filled)
 * 26–50  → Aging (watch closely)
 * 51–70  → Ghost candidate (90+ days with no sign of being filled)
 * 71–90  → Very likely ghost (6+ months)
 * 91–100 → Confirmed ghost (appears, vanishes, reappears OR 12+ months)
 */
export function scoreGhost(
  durationDays: number,
  archiveCount: number,
  reposted: boolean,
  repostCount = 0,
): { score: number; reason: string } {
  let score = 0;
  const reasons: string[] = [];

  // Base score from duration
  if (durationDays > 365) {
    score += 75;
    reasons.push(`posted for ${durationDays} days (over 1 year)`);
  } else if (durationDays > 180) {
    score += 60;
    reasons.push(`posted for ${durationDays} days (over 6 months)`);
  } else if (durationDays > 90) {
    score += 45;
    reasons.push(`posted for ${durationDays} days (over 3 months)`);
  } else if (durationDays > 60) {
    score += 25;
    reasons.push(`posted for ${durationDays} days`);
  } else if (durationDays > 30) {
    score += 10;
    reasons.push(`posted for ${durationDays} days`);
  }

  // Repost bonus scales with count: +15 first repost, +8 per additional (capped at +35 total)
  if (reposted) {
    const repostBonus = Math.min(35, 15 + (repostCount - 1) * 8);
    score += repostBonus;
    const times = repostCount > 1 ? `${repostCount}×` : 'once';
    reasons.push(`job disappeared then reappeared ${times} (reposted)`);
  }

  // High archive count relative to duration = long-lived posting with active crawls
  const checksPerMonth = archiveCount / Math.max(durationDays / 30, 1);
  if (checksPerMonth > 8 && durationDays > 60) {
    score += 5;
    reasons.push(`captured ${archiveCount}× over ${durationDays} days`);
  }

  score = Math.min(100, score);
  return {
    score,
    reason: reasons.join('; ') || 'normal posting duration',
  };
}

/**
 * Build a per-job persistence registry from a list of day-by-day results.
 * Tracks each unique job ID/title across snapshots to detect duration and reposting.
 */
export function buildJobRegistry(days: DayResult[]): Map<string, JobRecord> {
  // Key: job id if available, else normalized title
  const registry = new Map<string, JobRecord>();

  for (const day of days) {
    for (const job of day.jobs) {
      const key = job.id ?? job.url ?? normalizeTitle(job.title);

      const existing = registry.get(key);
      if (!existing) {
        registry.set(key, {
          title: job.title,
          url: job.url ?? '',
          id: job.id,
          location: job.location,
          department: job.department,
          firstSeen: day.date,
          lastSeen: day.date,
          durationDays: 0,
          archiveCount: 1,
          reposted: false,
          repostCount: 0,
          ghostScore: 0,
          ghostReason: '',
        });
      } else {
        // Check for gap (job disappeared for >30 days then came back)
        const gapDays = daysBetween(existing.lastSeen, day.date);
        if (gapDays > 30 && existing.lastSeen !== existing.firstSeen) {
          existing.reposted = true;
          existing.repostCount++;
        }
        existing.lastSeen = day.date;
        existing.archiveCount++;
      }
    }
  }

  // Compute final durations and ghost scores
  for (const record of registry.values()) {
    record.durationDays = daysBetween(record.firstSeen, record.lastSeen);
    const { score, reason } = scoreGhost(record.durationDays, record.archiveCount, record.reposted, record.repostCount);
    record.ghostScore = score;
    record.ghostReason = reason;
  }

  return registry;
}

/**
 * Compute summary statistics from a job registry.
 */
export function computeGhostStats(registry: Map<string, JobRecord>) {
  const jobs = [...registry.values()];

  const durations = jobs.map(j => j.durationDays).sort((a, b) => a - b);
  const median = durations.length > 0 ? durations[Math.floor(durations.length / 2)] : 0;
  const avg = durations.length > 0
    ? Math.round(durations.reduce((s, d) => s + d, 0) / durations.length)
    : 0;

  const ghosts = jobs.filter(j => j.ghostScore >= 70);

  const longestRunning = [...jobs]
    .sort((a, b) => b.durationDays - a.durationDays)
    .slice(0, 20);

  return {
    totalUniqueJobs: jobs.length,
    ghostCandidates: ghosts.length,
    ghostRate: jobs.length > 0 ? Math.round((ghosts.length / jobs.length) * 100) / 100 : 0,
    medianDurationDays: median,
    avgDurationDays: avg,
    longestRunningJobs: longestRunning,
  };
}

/**
 * Derive a simple timeline (unique job count per month) from the registry.
 */
export function registryToTimeline(registry: Map<string, JobRecord>): TimelinePoint[] {
  const monthCounts = new Map<string, number>();

  for (const job of registry.values()) {
    const months = monthsBetween(job.firstSeen, job.lastSeen);
    for (const month of months) {
      monthCounts.set(month, (monthCounts.get(month) ?? 0) + 1);
    }
  }

  return [...monthCounts.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, jobCount]) => ({ date, jobCount }));
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function normalizeTitle(title: string): string {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 80);
}

function monthsBetween(start: string, end: string): string[] {
  const result: string[] = [];
  const s = new Date(start);
  const e = new Date(end);

  const cur = new Date(s.getFullYear(), s.getMonth(), 1);
  while (cur <= e) {
    result.push(`${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, '0')}`);
    cur.setMonth(cur.getMonth() + 1);
  }
  return result;
}
