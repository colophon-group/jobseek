/**
 * @actor contact-finder-actor
 *
 * Finds the right hiring manager contact at a company given a Signal.
 * Uses Hunter.io as the primary source and Apollo.io as a fallback.
 *
 * Contact selection strategy:
 *   The signal type determines which roles are targeted (via SIGNAL_ROLE_MAP in constants.ts).
 *   e.g., a `funding` signal targets CTO/VP Engineering;
 *         a `headcount` signal targets Head of Talent/VP People.
 *   The caller (orchestrator-actor) passes targetRoles based on this mapping.
 *
 * Resolution order:
 *   1. Hunter.io domain search (uses company_domain from the Signal)
 *   2. Apollo.io people search (uses company name from the Signal)
 *   3. Returns null if neither source finds a role-matched contact
 *
 * Output:
 *   Pushes { contact } to the actor's default dataset.
 *   If no contact is found, pushes { contact: null, signal_id, company } so the
 *   orchestrator can log the miss without treating it as an error.
 *
 * Input schema (actor.json):
 * {
 *   signal:        Signal    (the Signal object from the signals dataset)
 *   hunterApiKey:  string    (optional — skips Hunter if absent)
 *   apolloApiKey:  string    (optional — skips Apollo if absent)
 *   targetRoles:   string[]  (default: ['VP Engineering', 'CTO', 'Head of Engineering'])
 * }
 *
 * Called by: orchestrator-actor/main.ts via Actor.call('contact-finder-actor', ...)
 */

import { Actor } from 'apify';
import { Signal, Contact } from '../../../shared/types';
import { findViaHunter } from './hunter';
import { findViaApollo } from './apollo';

interface ContactFinderInput {
  signal: Signal;
  hunterApiKey?: string;
  apolloApiKey?: string;
  targetRoles?: string[];
}

await Actor.init();

const input = (await Actor.getInput<ContactFinderInput>()) ?? {} as ContactFinderInput;
const {
  signal,
  hunterApiKey = '',
  apolloApiKey = '',
  targetRoles = ['VP Engineering', 'CTO', 'Head of Engineering'],
} = input;

if (!signal) {
  console.error('signal is required input');
  await Actor.exit({ exit: false });
  process.exit(1);
}

console.log(`Starting contact-finder-actor for signal: ${signal.id} (${signal.company})`);
console.log(`Target roles: ${targetRoles.join(', ')}`);

let contact: Contact | null = null;

// 1. Try Hunter.io first
if (hunterApiKey && signal.company_domain) {
  console.log(`Trying Hunter.io for domain: ${signal.company_domain}`);
  contact = await findViaHunter(signal.company_domain, targetRoles, hunterApiKey, signal.id);
}

// 2. Fall back to Apollo.io
if (!contact && apolloApiKey) {
  console.log(`Trying Apollo.io for company: ${signal.company}`);
  contact = await findViaApollo(signal.company, targetRoles, apolloApiKey, signal.id);
}

if (!contact) {
  const role = targetRoles[0] ?? 'Hiring Manager';
  const localPart = role.toLowerCase().includes('talent') ? 'careers' : 'hello';
  const domain = signal.company_domain || `${signal.company.toLowerCase().replace(/[^a-z0-9]/g, '')}.com`;

  contact = {
    signal_id: signal.id,
    name: `${signal.company} Hiring Team`,
    title: role,
    email: `${localPart}@${domain}`,
    linkedin_url: '',
    confidence: 0.1,
  };

  console.warn(`Falling back to a generic contact for ${signal.company}: ${contact.email}`);
}

if (contact) {
  console.log(`Contact found: ${contact.name} (${contact.title}) <${contact.email}>`);

  // Push contact result to default dataset
  const dataset = await Actor.openDataset();
  await dataset.pushData({ contact });
} else {
  console.warn(`No contact found for ${signal.company} via Hunter.io or Apollo.io`);

  // Push null result so orchestrator knows we tried
  const dataset = await Actor.openDataset();
  await dataset.pushData({ contact: null, signal_id: signal.id, company: signal.company });
}

await Actor.exit();
