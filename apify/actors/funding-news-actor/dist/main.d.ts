/**
 * @actor funding-news-actor
 *
 * Detects funding round signals from two sources:
 *   1. Crunchbase API  — structured data, requires API key (paid)
 *   2. RSS feeds       — TechCrunch Venture + VentureBeat Business (free, lower fidelity)
 *
 * Output: Signal[] written to the 'hiring-signals' Apify dataset.
 *
 * Signal type produced: `funding`
 *
 * Why funding signals matter:
 *   Companies that just closed a Series B/C/D typically hire aggressively in
 *   the 4–12 weeks after announcement. Reaching out in week 1–2 puts you ahead
 *   of the public job posting by months.
 *
 * Input schema (actor.json):
 * {
 *   crunchbaseApiKey:   string  (optional — skips Crunchbase if absent)
 *   minRoundAmountUsd:  number  (default: 10_000_000)
 *   roundTypes:         string[] (default: ['series_b','series_c','series_d','series_e'])
 *   lookbackDays:       number  (default: 7)
 * }
 *
 * Downstream: orchestrator-actor reads from 'hiring-signals', scores this actor's
 * output, and calls contact-finder-actor + email-drafter-actor for qualifying signals.
 */
export {};
//# sourceMappingURL=main.d.ts.map