/**
 * @module email-drafter-actor/prompt
 *
 * Drafts hyper-personalized speculative outreach emails using Claude (claude-sonnet-4-6).
 *
 * Email structure (4 required parts):
 *   1. Name the signal — specific concrete reference to the growth event
 *      (e.g., "I saw Stripe just closed a $50M Series C")
 *   2. Connect signal to hiring need — why this creates demand for this person's skills
 *   3. Tie sender's skills to that need — reference a specific past win that applies
 *   4. Ask for a 20-minute call — with a proposed time slot
 *
 * Constraints enforced via prompt:
 *   - Max 200 words in the body
 *   - Subject line ≤ 50 characters, specific to the signal (no generic openers)
 *   - Confident but conversational tone (not boastful, not stiff)
 *
 * Fallback behavior:
 *   If Claude returns malformed JSON, parseEmailResponse() falls back to
 *   buildFallbackBody() which assembles a minimal valid email without LLM.
 *   This ensures the pipeline never stalls on JSON parse failures.
 *
 * Signal-type-specific context:
 *   formatSignalContext() extracts type-specific fields from signal.raw
 *   so Claude has concrete numbers to reference (e.g., "$50M raised", "+25% headcount").
 */

import Anthropic from '@anthropic-ai/sdk';
import { Signal, Contact, UserProfile } from '../../../shared/types';

interface EmailDraft {
  subject: string;
  body: string;
}

/**
 * Drafts a personalized speculative outreach email using Claude.
 *
 * @param client      - Initialized Anthropic SDK client
 * @param signal      - The growth signal that triggered this outreach
 * @param contact     - The hiring manager the email is addressed to
 * @param userProfile - The job seeker's skills, background, and wins
 * @returns { subject, body } — both as plain text strings
 */
export async function draftEmail(
  client: Anthropic,
  signal: Signal,
  contact: Contact,
  userProfile: UserProfile
): Promise<EmailDraft> {
  const prompt = buildEmailPrompt(signal, contact, userProfile);

  const message = await client.messages.create({
    model: 'claude-sonnet-4-6',
    max_tokens: 1024,
    messages: [{ role: 'user', content: prompt }],
  });

  const rawText = message.content
    .filter((block) => block.type === 'text')
    .map((block) => (block as { type: 'text'; text: string }).text)
    .join('');

  return parseEmailResponse(rawText, signal, contact);
}

/**
 * Builds the full prompt sent to Claude.
 * Injects signal context, contact info, and user profile with specific formatting guidelines.
 */
function buildEmailPrompt(signal: Signal, contact: Contact, userProfile: UserProfile): string {
  const skillsList = userProfile.skills.join(', ');
  const pastWinsList = userProfile.pastWins
    .slice(0, 3)
    .map((w, i) => `${i + 1}. ${w}`)
    .join('\n');

  const signalContext = formatSignalContext(signal);

  return `You are a world-class executive recruiter writing a cold outreach email on behalf of a job seeker.

## Signal Context
${signalContext}

## Contact
- Name: ${contact.name}
- Title: ${contact.title}
- Company: ${signal.company}

## Sender's Profile
- Skills: ${skillsList}
- Background: ${userProfile.background}
- Notable Wins:
${pastWinsList}

## Email Requirements
Write a cold outreach email with exactly 4 parts:

**Part 1 — Name the Signal**: Open with a specific, concrete reference to what just happened at their company (the signal above). Be specific — mention the funding amount, the announcement, or the filing detail. Do NOT say "I saw your post" or generic openers.

**Part 2 — Connect Signal to Hiring Need**: In 1-2 sentences, explain what this signal implies about their hiring needs. E.g., a Series C means they'll need to scale engineering; an SEC filing mentioning "expanding our team" means headcount is incoming.

**Part 3 — Tie Sender's Skills to That Need**: In 2-3 sentences, connect the sender's specific skills and past wins directly to the need you identified. Be concrete — mention a specific win or skill that directly applies.

**Part 4 — Ask for a Call**: Close with a single, low-commitment ask: a 20-minute call to explore if there's a fit. Include a specific proposed time slot (e.g., "Tuesday or Wednesday next week").

## Tone Guidelines
- Confident but not boastful
- Conversational, not formal/stiff
- Max 200 words in the body
- Subject line: under 50 characters, specific to the signal (not generic like "Opportunity")

Respond with ONLY valid JSON in this exact format:
{
  "subject": "<email subject line>",
  "body": "<full email body, plain text, use newlines for paragraphs>"
}`;
}

/**
 * Formats signal details into a readable block for the prompt.
 * Adds type-specific context (e.g., dollar amounts for funding, % growth for headcount)
 * so Claude can reference concrete facts in the email.
 */
function formatSignalContext(signal: Signal): string {
  const lines = [
    `- Company: ${signal.company}`,
    `- Signal Type: ${signal.signal_type}`,
    `- What Happened: ${signal.signal_text}`,
    `- Date: ${new Date(signal.date).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}`,
    `- Source: ${signal.source_url}`,
  ];

  // Inject type-specific raw data so Claude has numbers to cite
  const raw = signal.raw;
  if (signal.signal_type === 'funding') {
    if (raw['money_raised_usd']) {
      lines.push(`- Amount Raised: $${(Number(raw['money_raised_usd']) / 1_000_000).toFixed(0)}M`);
    }
    if (raw['investment_type']) {
      lines.push(`- Round Type: ${String(raw['investment_type']).replace(/_/g, ' ')}`);
    }
  } else if (signal.signal_type === 'headcount') {
    lines.push(`- Previous Headcount: ${raw['previous_headcount']}`);
    lines.push(`- Current Headcount: ${raw['current_headcount']}`);
    lines.push(`- Growth: ${raw['delta_pct']}%`);
  } else if (signal.signal_type === 'job_gap') {
    lines.push(`- Gap Department: ${raw['department']}`);
    lines.push(`- Peer Average Openings: ${raw['peer_avg']}`);
  }

  return lines.join('\n');
}

/**
 * Parses Claude's JSON response into an EmailDraft.
 * Falls back to a template-based email if JSON parsing fails.
 */
function parseEmailResponse(rawText: string, signal: Signal, contact: Contact): EmailDraft {
  const jsonMatch = rawText.match(/\{[\s\S]*"subject"[\s\S]*"body"[\s\S]*\}/);
  if (jsonMatch) {
    try {
      const parsed = JSON.parse(jsonMatch[0]) as Partial<EmailDraft>;
      if (parsed.subject && parsed.body) {
        return {
          subject: parsed.subject.slice(0, 100),
          body: parsed.body,
        };
      }
    } catch {
      // fall through to fallback
    }
  }

  console.warn('Could not parse Claude email response as JSON, using fallback template');
  return {
    subject: buildFallbackSubject(signal),
    body: buildFallbackBody(signal, contact),
  };
}

/**
 * Generates a signal-type-specific fallback subject line.
 * Used when Claude's JSON response can't be parsed.
 */
export function buildFallbackSubject(signal: Signal): string {
  switch (signal.signal_type) {
    case 'funding':    return `Congrats on the round — quick question`;
    case 'headcount':  return `${signal.company}'s growth caught my eye`;
    case 'github':     return `Noticed ${signal.company}'s recent engineering activity`;
    case 'job_gap':    return `Potential opportunity at ${signal.company}`;
    case 'sec_filing': return `${signal.company}'s growth plans`;
    default:           return `${signal.company} — let's connect`;
  }
}

/**
 * Generates a minimal fallback email body using the signal text directly.
 * Used when Claude's JSON response can't be parsed.
 */
export function buildFallbackBody(signal: Signal, contact: Contact): string {
  const firstName = contact.name.split(' ')[0];
  return [
    `Hi ${firstName},`,
    '',
    signal.signal_text,
    '',
    `I noticed this and wanted to reach out because my background aligns well with the needs this typically creates.`,
    '',
    `Would you be open to a 20-minute call next week to explore whether there's a fit?`,
    '',
    `Best,`,
  ].join('\n');
}
