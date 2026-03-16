/**
 * @module orchestrator-actor/decay
 *
 * Time-based signal score decay.
 *
 * Rationale:
 *   A funding announcement is most actionable in the first 1–2 weeks.
 *   After 3 weeks, the company has typically started internal discussions
 *   and posted jobs are imminent. The decay function penalizes stale signals
 *   so the orchestrator prioritizes fresh intelligence.
 *
 * Decay model:
 *   Exponential decay per week:
 *     decayed = score × (1 - SIGNAL_DECAY_RATE)^weeksElapsed
 *   Where SIGNAL_DECAY_RATE = 0.3 (30% reduction per week)
 *
 * Example decay curve (starting score = 8):
 *   Week 0: 8.0
 *   Week 1: 5.6
 *   Week 2: 3.9
 *   Week 3: 2.7
 *   Week 4: 1.9 (floors at 1.0 after sufficient time)
 *
 * The floor of 1 ensures even old signals aren't completely discarded —
 * a very high initial score can still survive several weeks.
 */

import { SIGNAL_DECAY_RATE } from '../../../shared/constants';

/**
 * Applies time-based exponential decay to a signal score.
 * Score reduces by SIGNAL_DECAY_RATE (30%) per elapsed week.
 * Returns a minimum of 1.0 regardless of age.
 *
 * @param score      - Original Claude-assigned score (1–10)
 * @param signalDate - ISO 8601 date string of when the signal occurred
 * @returns Decayed score in range [1, score]
 */
export function applyDecay(score: number, signalDate: string): number {
  const now = new Date();
  const signal = new Date(signalDate);

  if (isNaN(signal.getTime())) {
    console.warn(`Invalid signal date: "${signalDate}", returning original score`);
    return score;
  }

  const msElapsed = now.getTime() - signal.getTime();
  const weeksElapsed = msElapsed / (7 * 24 * 60 * 60 * 1000);

  if (weeksElapsed <= 0) return score; // Future-dated signal — no decay

  // Exponential decay formula
  const decayFactor = Math.pow(1 - SIGNAL_DECAY_RATE, weeksElapsed);
  const decayedScore = score * decayFactor;

  return Math.max(1, parseFloat(decayedScore.toFixed(2)));
}

/**
 * Returns the number of weeks elapsed since a signal date.
 * Used by orchestrator for logging/debugging purposes.
 *
 * @param signalDate - ISO 8601 date string
 * @returns Weeks elapsed (float), or 0 if date is invalid/in the future
 */
export function weeksElapsed(signalDate: string): number {
  const now = new Date();
  const signal = new Date(signalDate);
  if (isNaN(signal.getTime())) return 0;
  return Math.max(0, (now.getTime() - signal.getTime()) / (7 * 24 * 60 * 60 * 1000));
}
