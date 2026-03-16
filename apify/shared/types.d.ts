/**
 * @module shared/types
 *
 * Shared TypeScript interfaces used across all actors in the hiring signal pipeline.
 *
 * Data flow:
 *   Signal  →  scored by orchestrator  →  Contact  →  OutreachDraft
 *
 * All signals are written to the Apify dataset named 'hiring-signals'.
 * All outreach drafts are written to the Apify dataset named 'outreach-ready'.
 */
/**
 * A hiring signal — a detected growth event at a company that implies an
 * imminent or likely hiring need.
 *
 * Produced by: funding-news-actor, sec-edgar-actor, twitter-x-actor,
 *              linkedin-headcount-actor, github-signal-actor, jobboard-gap-actor
 * Consumed by: orchestrator-actor
 *
 * The `id` field is a 16-char hex SHA-256 hash of `company:signal_type:date`
 * and is used for deduplication across runs.
 */
export interface Signal {
    /** 16-char hex SHA-256 of `company:signal_type:date` — used for dedup */
    id: string;
    /** Human-readable company name, e.g. "Stripe" */
    company: string;
    /** Best-effort domain guess, e.g. "stripe.com" — used by contact-finder */
    company_domain: string;
    /**
     * Category of signal:
     * - `funding`    — Series B/C/D/E round detected via Crunchbase or RSS
     * - `sec_filing` — Hiring/expansion language found in 10-K or 10-Q
     * - `twitter`    — CTO/founder tweet about scaling, new office, or hiring
     * - `headcount`  — LinkedIn headcount grew ≥N% since last snapshot
     * - `github`     — New repos, new stack languages, or contributor surge
     * - `job_gap`    — Target has 0 openings in a dept where peers average >2
     */
    signal_type: 'funding' | 'sec_filing' | 'twitter' | 'headcount' | 'github' | 'job_gap';
    /** One-sentence human-readable summary of the signal event */
    signal_text: string;
    /** URL to the original source (article, filing, tweet, LinkedIn page, etc.) */
    source_url: string;
    /** ISO 8601 date string of when the signal occurred */
    date: string;
    /** Full source payload — structure varies by signal_type (see individual actors for shape) */
    raw: Record<string, unknown>;
    /**
     * Relevance score 1–10, set by orchestrator-actor/scorer.ts via Claude.
     * Higher = more relevant to the user's profile. Undefined until scored.
     */
    score?: number;
    /**
     * Time-decay multiplier (0–1), applied by orchestrator-actor/decay.ts.
     * Older signals get a lower factor. Undefined until decay is applied.
     */
    decay_factor?: number;
}
/**
 * A hiring manager or decision-maker contact found for a given signal.
 *
 * Produced by: contact-finder-actor (via Hunter.io or Apollo.io)
 * Consumed by: email-drafter-actor, orchestrator-actor
 */
export interface Contact {
    /** ID of the Signal this contact was found for */
    signal_id: string;
    /** Full name, e.g. "Jane Smith" */
    name: string;
    /** Job title, e.g. "VP of Engineering" */
    title: string;
    /** Verified or best-guess email address */
    email: string;
    /** LinkedIn profile URL, may be empty string if not found */
    linkedin_url: string;
    /**
     * Confidence score 0–1 for the email being deliverable/correct.
     * Derived from Hunter.io's confidence % or Apollo.io's email_status.
     */
    confidence: number;
}
/**
 * A completed outreach email draft waiting for user review.
 *
 * Produced by: email-drafter-actor (using Claude claude-sonnet-4-6)
 * Consumed by: user (review → send)
 *
 * Written to both the default actor dataset and the shared 'outreach-ready' dataset.
 */
export interface OutreachDraft {
    /** ID of the Signal that triggered this outreach */
    signal_id: string;
    /** The contact this email is addressed to */
    contact: Contact;
    /** Email subject line (≤50 chars, specific to the signal) */
    subject: string;
    /**
     * Full email body in plain text. Structured as 4 parts:
     *   1. Name the signal (concrete reference to the growth event)
     *   2. Connect signal to a hiring need
     *   3. Tie sender's skills to that need
     *   4. Ask for a 20-minute call
     */
    body: string;
    /**
     * Lifecycle status:
     * - `pending_review` — draft is ready, user has not acted on it yet
     * - `sent`           — user approved and sent the email
     * - `replied`        — contact replied (manually tracked)
     */
    status: 'pending_review' | 'sent' | 'replied';
}
/**
 * The job seeker's profile — used by the orchestrator for scoring
 * and by the email drafter for personalization.
 *
 * Passed as input to orchestrator-actor and email-drafter-actor.
 */
export interface UserProfile {
    /** List of technical/professional skills, e.g. ["Go", "Kubernetes", "data pipelines"] */
    skills: string[];
    /** 1-2 sentence background summary, e.g. "Staff engineer with 8 years scaling data platforms" */
    background: string;
    /** Up to 3 concrete, quantified wins — used in the email body */
    pastWins: string[];
}
/**
 * Maps signal types to the roles most likely to be the right outreach target.
 * Used by orchestrator-actor to pass targetRoles to contact-finder-actor.
 */
export type SignalTypeRoleMap = Record<Signal['signal_type'], string[]>;
//# sourceMappingURL=types.d.ts.map