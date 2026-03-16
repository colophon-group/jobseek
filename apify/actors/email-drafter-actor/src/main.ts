/**
 * @actor email-drafter-actor
 *
 * Drafts a personalized speculative outreach email for a qualifying signal
 * using Claude (claude-sonnet-4-6) via the Anthropic SDK.
 *
 * The email is structured in 4 required parts (enforced in prompt.ts):
 *   1. Name the specific signal event (proves the sender did real homework)
 *   2. Connect the signal to an implied hiring need
 *   3. Tie the sender's skills/wins directly to that need
 *   4. Ask for a 20-minute call with a specific proposed time
 *
 * Output:
 *   - Pushes { draft: OutreachDraft } to the actor's default dataset
 *     (orchestrator reads from here via the run's defaultDatasetId)
 *   - Also pushes OutreachDraft directly to the shared 'outreach-ready' dataset
 *     (for direct browsing/export without going through the orchestrator)
 *   - draft.status is always set to 'pending_review' — never auto-sent
 *
 * Input schema (actor.json):
 * {
 *   signal:          Signal       (full Signal object)
 *   contact:         Contact      (contact found by contact-finder-actor)
 *   userProfile:     UserProfile  (job seeker's skills, background, wins)
 *   anthropicApiKey: string       (required — Claude API key)
 * }
 *
 * Called by: orchestrator-actor/main.ts via Actor.call('email-drafter-actor', ...)
 */

import { Actor } from 'apify';
import Anthropic from '@anthropic-ai/sdk';
import { Signal, Contact, UserProfile, OutreachDraft } from '../../../shared/types';
import { DATASETS } from '../../../shared/constants';
import { buildFallbackBody, buildFallbackSubject, draftEmail } from './prompt';
import { pushDataWithFallback } from '../../../shared/storage';

interface EmailDrafterInput {
  signal: Signal;
  contact: Contact;
  userProfile: UserProfile;
  anthropicApiKey: string;
}

await Actor.init();

const input = (await Actor.getInput<EmailDrafterInput>()) ?? {} as EmailDrafterInput;
const { signal, contact, userProfile, anthropicApiKey } = input;

if (!signal || !contact || !userProfile) {
  console.error('Missing required inputs: signal, contact, userProfile');
  await Actor.exit({ exit: false });
  process.exit(1);
}

console.log(`Starting email-drafter-actor for signal ${signal.id} — ${signal.company}`);
console.log(`Drafting email to: ${contact.name} (${contact.title})`);

let emailDraft: { subject: string; body: string };

if (!anthropicApiKey) {
  console.warn('No anthropicApiKey provided, using fallback email template');
  emailDraft = {
    subject: buildFallbackSubject(signal),
    body: buildFallbackBody(signal, contact),
  };
} else {
  const anthropic = new Anthropic({ apiKey: anthropicApiKey });
  try {
    emailDraft = await draftEmail(anthropic, signal, contact, userProfile);
    console.log(`Email drafted successfully — subject: "${emailDraft.subject}"`);
  } catch (err) {
    console.error('Fatal error drafting email, using fallback template:', err);
    emailDraft = {
      subject: buildFallbackSubject(signal),
      body: buildFallbackBody(signal, contact),
    };
  }
}

const outreachDraft: OutreachDraft = {
  signal_id: signal.id,
  contact,
  subject: emailDraft.subject,
  body: emailDraft.body,
  status: 'pending_review',
};

// Push to default dataset (orchestrator reads from here)
await pushDataWithFallback([{ draft: outreachDraft }]);
await pushDataWithFallback([outreachDraft], DATASETS.OUTREACH);

console.log(`Outreach draft pushed to datasets`);
console.log(`Subject: ${emailDraft.subject}`);
console.log(`Body preview: ${emailDraft.body.slice(0, 100)}...`);

await Actor.exit();
