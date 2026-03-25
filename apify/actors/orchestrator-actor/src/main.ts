import { Actor } from 'apify';
import Anthropic from '@anthropic-ai/sdk';
import { Signal, UserProfile, Contact, OutreachDraft } from '../../../shared/types';
import { DATASETS, SIGNAL_ROLE_MAP } from '../../../shared/constants';
import { pushDataWithFallback } from '../../../shared/storage';
import { sleep } from '../../../shared/utils';
import { scoreSignal } from './scorer';
import { applyDecay } from './decay';

interface OrchestratorInput {
  anthropicApiKey?: string;
  userProfile: UserProfile;
  scoreThreshold?: number;
  hunterApiKey?: string;
  apolloApiKey?: string;
  lookbackDays?: number;
  runIngestionActors?: boolean;
  secCompanies?: string[];
  githubOrgs?: string[];
  xHandles?: string[];
  keywords?: string[];
  targetCompanies?: string[];
  peerCompanies?: string[];
  linkedinCompanyUrls?: string[];
  linkedinCookies?: string;
  crunchbaseApiKey?: string;
  githubToken?: string;
  actorNamespace?: string;
  /** Crunchbase category slugs to filter funding rounds (e.g. ['blockchain', 'artificial-intelligence']) */
  fundingCategories?: string[];
  /** Minimum funding round amount in USD (default: 1M) */
  minFundingAmountUsd?: number;
  /** Funding round types to include (default: seed through series_e) */
  fundingRoundTypes?: string[];
}

interface ContactFinderOutput {
  contact?: Contact;
}

interface EmailDrafterOutput {
  draft?: OutreachDraft;
}

await Actor.init();

const input = (await Actor.getInput<OrchestratorInput>()) ?? {} as OrchestratorInput;
const {
  anthropicApiKey,
  userProfile,
  scoreThreshold = 4,
  hunterApiKey = '',
  apolloApiKey = '',
  lookbackDays = 14,
  runIngestionActors = true,
  secCompanies = [],
  githubOrgs = [],
  xHandles = [],
  keywords = [],
  targetCompanies = [],
  peerCompanies = [],
  linkedinCompanyUrls = [],
  linkedinCookies = '',
  crunchbaseApiKey = '',
  githubToken = '',
  actorNamespace = 'golanger',
  fundingCategories,
  minFundingAmountUsd = 1_000_000,
  fundingRoundTypes = ['seed', 'pre_seed', 'series_a', 'series_b', 'series_c', 'series_d', 'series_e'],
} = input;

if (!userProfile?.skills || !userProfile?.background) {
  console.error('userProfile with skills and background is required');
  await Actor.exit({ exit: false });
  process.exit(1);
}

console.log(`Starting orchestrator: scoreThreshold=${scoreThreshold}, lookbackDays=${lookbackDays}`);

const anthropic = anthropicApiKey ? new Anthropic({ apiKey: anthropicApiKey }) : null;
const allSignals = runIngestionActors
  ? await collectSignalsFromSourceActors({
      accountUsername: actorNamespace,
      lookbackDays,
      secCompanies,
      githubOrgs,
      xHandles,
      keywords,
      targetCompanies,
      peerCompanies,
      linkedinCompanyUrls,
      linkedinCookies,
      crunchbaseApiKey,
      githubToken,
      fundingCategories,
      minFundingAmountUsd,
      fundingRoundTypes,
    })
  : [];
console.log(`Loaded ${allSignals.length} raw signals from source actors`);

// 2. Filter to signals within the lookback window
const cutoff = new Date();
cutoff.setDate(cutoff.getDate() - lookbackDays);

const recentSignals = allSignals.filter((s) => {
  try {
    return new Date(s.date) >= cutoff;
  } catch {
    return false;
  }
});
console.log(`${recentSignals.length} signals within the last ${lookbackDays} days`);

// 3. Deduplicate by id
const signalMap = new Map<string, Signal>();
for (const signal of recentSignals) {
  if (!signalMap.has(signal.id)) {
    signalMap.set(signal.id, signal);
  }
}
const deduped = Array.from(signalMap.values());
console.log(`${deduped.length} unique signals after deduplication`);

// 4. Score and decay each signal
const scored: Array<{ signal: Signal; finalScore: number; reasoning: string }> = [];

for (const signal of deduped) {
  console.log(`Scoring signal: ${signal.id} (${signal.signal_type} — ${signal.company})`);

  const { score, reasoning } = await scoreSignal(anthropic, signal, userProfile);
  const decayedScore = applyDecay(score, signal.date);

  console.log(`  Raw score: ${score}, Decayed: ${decayedScore}, Reasoning: ${reasoning.slice(0, 80)}...`);

  scored.push({ signal, finalScore: decayedScore, reasoning });

  // Small delay to avoid rate limiting Claude
  await sleep(200);
}

// 5. Filter to signals meeting the threshold
const qualifying = scored.filter((s) => s.finalScore >= scoreThreshold);
console.log(`${qualifying.length} signals meet score threshold of ${scoreThreshold}`);

// 6. Process qualifying signals
for (const { signal, finalScore, reasoning } of qualifying) {
  console.log(`\nProcessing qualifying signal: ${signal.company} (score: ${finalScore})`);

  // Determine target roles for this signal type
  const targetRoles = SIGNAL_ROLE_MAP[signal.signal_type] ?? ['VP Engineering', 'CTO'];

  // 6a. Call contact-finder-actor
  let contact: Contact | null = null;
  if (hunterApiKey || apolloApiKey) {
    try {
      const contactRun = await Actor.call(resolveActorName(actorNamespace, 'contact-finder-actor'), {
        signal,
        hunterApiKey,
        apolloApiKey,
        targetRoles,
      });

      if (contactRun?.defaultDatasetId) {
        const contactDataset = await Actor.openDataset(contactRun.defaultDatasetId);
        const { items: contactItems } = await contactDataset.getData();
        const result = contactItems[0] as ContactFinderOutput | undefined;
        contact = result?.contact ?? null;
      }
    } catch (err) {
      console.error(`Error finding contact for ${signal.company}:`, err);
    }
  } else {
    contact = buildFallbackContact(signal, targetRoles);
  }

  if (!contact) {
    console.warn(`No contact found for ${signal.company}, skipping email draft`);
    continue;
  }

  console.log(`  Found contact: ${contact.name} (${contact.title})`);

  // 6b. Call email-drafter-actor
  let draft: OutreachDraft | null = null;
  if (anthropicApiKey) {
    try {
      const emailRun = await Actor.call(resolveActorName(actorNamespace, 'email-drafter-actor'), {
        signal,
        contact,
        userProfile,
        anthropicApiKey,
      });

      if (emailRun?.defaultDatasetId) {
        const emailDataset = await Actor.openDataset(emailRun.defaultDatasetId);
        const { items: emailItems } = await emailDataset.getData();
        const result = emailItems[0] as EmailDrafterOutput | undefined;
        draft = result?.draft ?? null;
      }
    } catch (err) {
      console.error(`Error drafting email for ${signal.company}:`, err);
    }
  } else {
    draft = buildFallbackDraft(signal, contact);
  }

  if (!draft) {
    console.warn(`No email draft generated for ${signal.company}`);
    continue;
  }

  // 6c. Push enriched result to outreach-ready dataset
  const outreachRecord = {
    ...draft,
    signal_id: signal.id,
    signal_company: signal.company,
    signal_type: signal.signal_type,
    signal_text: signal.signal_text,
    signal_date: signal.date,
    source_url: signal.source_url,
    careers_url: signal.careers_url,
    final_score: finalScore,
    scoring_reasoning: reasoning,
    contact,
    status: 'pending_review' as const,
    created_at: new Date().toISOString(),
  };

  await pushDataWithFallback([outreachRecord], DATASETS.OUTREACH);
  console.log(`  Pushed outreach draft for ${signal.company} — subject: "${draft.subject}"`);
}

console.log(`\nOrchestrator complete. Processed ${qualifying.length} qualifying signals.`);

await Actor.exit();

interface SourceRunInput {
  accountUsername?: string;
  lookbackDays: number;
  secCompanies: string[];
  githubOrgs: string[];
  xHandles: string[];
  keywords: string[];
  targetCompanies: string[];
  peerCompanies: string[];
  linkedinCompanyUrls: string[];
  linkedinCookies: string;
  crunchbaseApiKey: string;
  githubToken: string;
  fundingCategories?: string[];
  minFundingAmountUsd: number;
  fundingRoundTypes: string[];
}

async function collectSignalsFromSourceActors(input: SourceRunInput): Promise<Signal[]> {
  const allSignals: Signal[] = [];
  await collectActorOutput('funding-news-actor', {
      crunchbaseApiKey: input.crunchbaseApiKey,
      lookbackDays: input.lookbackDays,
      minRoundAmountUsd: input.minFundingAmountUsd,
      roundTypes: input.fundingRoundTypes,
      fundingCategories: input.fundingCategories,
    }, allSignals, input.accountUsername);

  await collectActorOutput('sec-edgar-actor', {
      companies: input.secCompanies,
      lookbackDays: input.lookbackDays,
    }, allSignals, input.accountUsername);

  if (input.githubOrgs.length > 0) {
    await collectActorOutput('github-signal-actor', {
      githubOrgs: input.githubOrgs,
      githubToken: input.githubToken,
      lookbackDays: input.lookbackDays,
    }, allSignals, input.accountUsername);
  }

  if (input.xHandles.length > 0 || input.keywords.length > 0) {
    await collectActorOutput('twitter-x-actor', {
      xHandles: input.xHandles,
      keywords: input.keywords,
      lookbackDays: input.lookbackDays,
    }, allSignals, input.accountUsername);
  }

  if (input.linkedinCompanyUrls.length > 0) {
    await collectActorOutput('linkedin-headcount-actor', {
      companyUrls: input.linkedinCompanyUrls,
    }, allSignals, input.accountUsername);
  }

  if (input.targetCompanies.length > 0 && input.peerCompanies.length > 0) {
    await collectActorOutput('jobboard-gap-actor', {
      targetCompanies: input.targetCompanies,
      peerCompanies: input.peerCompanies,
      linkedinCookies: input.linkedinCookies,
    }, allSignals, input.accountUsername);
  }
  return allSignals;
}

async function collectActorOutput(
  actorName: string,
  actorInput: Record<string, unknown>,
  sink: Signal[],
  accountUsername?: string,
): Promise<void> {
  try {
    console.log(`Running source actor: ${actorName}`);
    const run = await Actor.call(resolveActorName(accountUsername, actorName), actorInput);
    if (!run?.defaultDatasetId) return;

    const dataset = await Actor.openDataset(run.defaultDatasetId);
    const { items } = await dataset.getData({ clean: true });
    sink.push(...(items as Signal[]));
    console.log(`Collected ${items.length} signals from ${actorName}`);
  } catch (err) {
    console.error(`Source actor failed: ${actorName}`, err);
  }
}

function resolveActorName(accountUsername: string | undefined, actorName: string): string {
  return accountUsername ? `${accountUsername}/${actorName}` : actorName;
}

function buildFallbackContact(signal: Signal, targetRoles: string[]): Contact {
  const role = targetRoles[0] ?? 'Hiring Manager';
  const localPart = role.toLowerCase().includes('talent') ? 'careers' : 'hello';
  const domain = signal.company_domain || `${signal.company.toLowerCase().replace(/[^a-z0-9]/g, '')}.com`;

  return {
    signal_id: signal.id,
    name: `${signal.company} Hiring Team`,
    title: role,
    email: `${localPart}@${domain}`,
    linkedin_url: '',
    confidence: 0.1,
  };
}

function buildFallbackDraft(signal: Signal, contact: Contact): OutreachDraft {
  const firstName = contact.name.split(' ')[0];
  return {
    signal_id: signal.id,
    contact,
    subject: signal.signal_type === 'funding'
      ? 'Congrats on the round — quick question'
      : `${signal.company} caught my eye`,
    body: [
      `Hi ${firstName},`,
      '',
      signal.signal_text,
      '',
      "I noticed this and wanted to reach out because my background aligns well with the kind of work this usually creates.",
      '',
      'Would you be open to a 20-minute call next week to explore whether there might be a fit?',
      '',
      'Best,',
    ].join('\n'),
    status: 'pending_review',
  };
}
