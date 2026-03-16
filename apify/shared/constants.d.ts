/**
 * @module shared/constants
 *
 * Shared configuration constants used across all actors.
 *
 * Import pattern:
 *   import { DATASETS, SIGNAL_ROLE_MAP, SIGNAL_DECAY_RATE, MIN_SIGNAL_SCORE } from '../../../shared/constants';
 */
/**
 * Maps each signal type to the job titles most likely to be the right
 * outreach target at the company. Used by orchestrator-actor to pass
 * `targetRoles` to contact-finder-actor.
 *
 * Logic behind the mapping:
 * - `funding`    → Engineering/talent leaders who will be growing headcount fastest
 * - `sec_filing` → VPs and CPOs who authored the growth language in the filing
 * - `twitter`    → CTO/product since they typically post the growth tweets
 * - `headcount`  → People/HR leaders managing the growth
 * - `github`     → Platform/infra leads who opened the new repos
 * - `job_gap`    → Engineering leaders whose team has the gap
 */
export declare const SIGNAL_ROLE_MAP: Record<string, string[]>;
/**
 * Fractional score reduction applied per elapsed week to a signal's score.
 * 0.3 = 30% reduction per week (exponential decay).
 *
 * Formula in orchestrator: score * (1 - SIGNAL_DECAY_RATE)^weeksElapsed
 *
 * Week 0: score × 1.00
 * Week 1: score × 0.70
 * Week 2: score × 0.49
 * Week 3: score × 0.34
 *
 * Rationale: A Series C is most actionable in the first 1-2 weeks.
 * After 3 weeks, most companies have already started interviewing internally.
 */
export declare const SIGNAL_DECAY_RATE = 0.3;
/**
 * Minimum final score (after Claude scoring + decay) required for a signal
 * to proceed to contact finding and email drafting.
 *
 * Default: 7 out of 10. Overridable via orchestrator input `scoreThreshold`.
 */
export declare const MIN_SIGNAL_SCORE = 7;
/**
 * Named Apify datasets shared across all actors.
 *
 * - SIGNALS:  All raw signals from the 6 ingestion actors (append-only, deduped by orchestrator)
 * - OUTREACH: Completed outreach drafts ready for user review and sending
 *
 * Access in any actor:
 *   const dataset = await Actor.openDataset(DATASETS.SIGNALS);
 */
export declare const DATASETS: {
    readonly SIGNALS: "hiring-signals";
    readonly OUTREACH: "outreach-ready";
};
//# sourceMappingURL=constants.d.ts.map