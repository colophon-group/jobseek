/**
 * @module orchestrator-actor/scorer
 *
 * Uses Claude (claude-sonnet-4-6) to score each signal's relevance
 * for a specific job seeker profile on a scale of 1–10.
 *
 * Why use an LLM for scoring (vs. a simpler rule-based approach)?
 *   Rule-based scoring can't reason about:
 *   - Whether a funding signal in fintech is relevant to a data engineer
 *   - Whether a headcount spike at an AI company suggests ML infra roles
 *   - Nuanced fit between the job seeker's past wins and the company's growth area
 *   Claude can weigh all of these contextually.
 *
 * Prompt structure:
 *   - Signal details (company, type, text, date, source)
 *   - User's skill list, background summary, and up to 3 past wins
 *   - Scoring rubric (1–3: low, 4–6: moderate, 7–9: high, 10: perfect)
 *   - Three evaluation questions to guide reasoning
 *   - JSON-only response format: { "score": N, "reasoning": "..." }
 *
 * Fallback behavior:
 *   If Claude fails or returns malformed JSON, a default score is assigned
 *   based on signal type (from getDefaultScore). This ensures the pipeline
 *   doesn't halt on transient API errors.
 *
 * Rate limiting note:
 *   The orchestrator adds a 200ms sleep between each Claude call to avoid
 *   hitting the API's requests-per-minute limit.
 */
import Anthropic from '@anthropic-ai/sdk';
import { Signal, UserProfile } from '../../../shared/types';
/**
 * Scores a signal's relevance for the given user profile using Claude.
 *
 * @param client      - Initialized Anthropic SDK client
 * @param signal      - The signal to evaluate
 * @param userProfile - The job seeker's skills, background, and wins
 * @returns { score: 1–10, reasoning: string }
 */
export declare function scoreSignal(client: Anthropic, signal: Signal, userProfile: UserProfile): Promise<{
    score: number;
    reasoning: string;
}>;
//# sourceMappingURL=scorer.d.ts.map