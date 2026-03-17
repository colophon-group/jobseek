"use strict";
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
Object.defineProperty(exports, "__esModule", { value: true });
exports.scoreSignal = scoreSignal;
/**
 * Scores a signal's relevance for the given user profile using Claude.
 *
 * @param client      - Initialized Anthropic SDK client
 * @param signal      - The signal to evaluate
 * @param userProfile - The job seeker's skills, background, and wins
 * @returns { score: 1–10, reasoning: string }
 */
async function scoreSignal(client, signal, userProfile) {
    const prompt = buildScoringPrompt(signal, userProfile);
    try {
        const message = await client.messages.create({
            model: 'claude-sonnet-4-6',
            max_tokens: 512,
            messages: [{ role: 'user', content: prompt }],
        });
        const rawText = message.content
            .filter((block) => block.type === 'text')
            .map((block) => block.text)
            .join('');
        const parsed = parseJsonResponse(rawText);
        return {
            score: clamp(parsed.score, 1, 10),
            reasoning: parsed.reasoning ?? 'No reasoning provided',
        };
    }
    catch (err) {
        console.error('Error calling Claude for signal scoring:', err);
        return {
            score: getDefaultScore(signal.signal_type),
            reasoning: 'Claude scoring failed; using fallback score',
        };
    }
}
/**
 * Builds the scoring prompt sent to Claude.
 * Includes all signal metadata and the user's full profile.
 */
function buildScoringPrompt(signal, userProfile) {
    const skillsList = userProfile.skills.join(', ');
    const pastWinsList = userProfile.pastWins.map((w, i) => `${i + 1}. ${w}`).join('\n');
    return `You are evaluating whether a hiring signal is relevant for a job seeker to act on.

## Signal Details
- Company: ${signal.company}
- Signal Type: ${signal.signal_type}
- Signal Text: ${signal.signal_text}
- Date: ${signal.date}
- Source: ${signal.source_url}

## Job Seeker Profile
- Skills: ${skillsList}
- Background: ${userProfile.background}
- Past Wins:
${pastWinsList}

## Your Task
Score this signal's relevance for this job seeker on a scale of 1-10, where:
- 1-3: Low relevance (wrong industry, skills don't match, signal is weak)
- 4-6: Moderate relevance (some skill overlap, signal is real but indirect)
- 7-9: High relevance (strong skill match, clear hiring need implied)
- 10: Perfect relevance (ideal match, explicit hiring signal, skills are directly needed)

Consider:
1. Does this signal type typically create roles matching this person's skills?
2. Is the company's growth trajectory likely to create demand for their background?
3. How specific and credible is the signal?

Respond with ONLY valid JSON in this exact format:
{"score": <number 1-10>, "reasoning": "<one to two sentence explanation>"}`;
}
/**
 * Extracts a ScoreResponse from Claude's raw text output.
 * Handles cases where Claude wraps JSON in markdown code blocks or adds surrounding text.
 */
function parseJsonResponse(text) {
    // Primary: look for JSON object containing both fields
    const jsonMatch = text.match(/\{[\s\S]*"score"[\s\S]*"reasoning"[\s\S]*\}/);
    if (jsonMatch) {
        try {
            const parsed = JSON.parse(jsonMatch[0]);
            if (typeof parsed.score === 'number' && typeof parsed.reasoning === 'string') {
                return { score: parsed.score, reasoning: parsed.reasoning };
            }
        }
        catch {
            // fall through to field-level extraction
        }
    }
    // Fallback: extract fields individually via regex
    const scoreMatch = text.match(/"score"\s*:\s*(\d+(?:\.\d+)?)/);
    const reasoningMatch = text.match(/"reasoning"\s*:\s*"([^"]+)"/);
    return {
        score: scoreMatch ? parseFloat(scoreMatch[1]) : 5,
        reasoning: reasoningMatch ? reasoningMatch[1] : text.slice(0, 200),
    };
}
/** Clamps a number to [min, max] */
function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
}
/**
 * Default scores by signal type — used when Claude scoring fails.
 * Reflects approximate expected relevance assuming a generalist tech background.
 */
function getDefaultScore(signalType) {
    const defaults = {
        funding: 7, // High: funding almost always precedes hiring
        sec_filing: 6, // Moderate: disclosures are real but often vague
        twitter: 5, // Moderate: noisy; keyword scorer already filtered
        headcount: 7, // High: direct evidence of growth
        github: 6, // Moderate: engineering activity but no direct hiring signal
        job_gap: 8, // High: gap vs peers is a strong predictor of imminent hire
    };
    return defaults[signalType] ?? 5;
}
//# sourceMappingURL=scorer.js.map