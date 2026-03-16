/**
 * @actor github-signal-actor
 *
 * Analyzes GitHub organization activity to detect engineering growth signals.
 *
 * Three signal types detected per org:
 *   1. New repos  — org created N new repos in the lookback window
 *      (rapid repo creation = new product lines or teams spinning up)
 *   2. Stack change — new programming languages appearing in recent repos
 *      vs. historic repos (e.g., Rust appearing at a Go shop = new infra team)
 *   3. Contributor surge — high external contributor count across active repos
 *      (open-source activity often precedes a dedicated OSS/platform hire)
 *
 * Signal type produced: `github`
 *
 * Org resolution:
 *   Inputs can be GitHub org handles ("stripe") OR company names ("Stripe Inc.").
 *   Company names with spaces or >39 chars are resolved via orgMapper.ts.
 *
 * Input schema (actor.json):
 * {
 *   githubOrgs:   string[]  (org handles or company names)
 *   githubToken:  string    (PAT — increases rate limit from 60 to 5000 req/hr)
 *   lookbackDays: number    (default: 14)
 * }
 *
 * Rate limiting:
 *   A 1000ms sleep is added between orgs to avoid hitting the GitHub API limit.
 *   With a token, the limit is 5000 req/hr — sufficient for dozens of orgs per run.
 */

import { Actor } from 'apify';
import { Octokit } from '@octokit/rest';
import { createHash } from 'crypto';
import { Signal } from '../../../shared/types';
import { DATASETS } from '../../../shared/constants';
import { pushDataWithFallback } from '../../../shared/storage';
import { resolveGithubOrg } from './orgMapper';

interface GithubSignalInput {
  githubOrgs?: string[];
  githubToken?: string;
  lookbackDays?: number;
}

interface RepoInfo {
  name: string;
  full_name: string;
  created_at: string | null;
  updated_at: string | null;
  language: string | null;
  languages_url: string;
  forks_count: number;
  stargazers_count: number;
  html_url: string;
  description: string | null;
  fork: boolean;
}

await Actor.init();

const input = (await Actor.getInput<GithubSignalInput>()) ?? {};
const { githubOrgs = [], githubToken = '', lookbackDays = 14 } = input;

if (githubOrgs.length === 0) {
  console.warn('No githubOrgs provided. Exiting.');
  await Actor.exit();
  process.exit(0);
}

console.log(`Starting github-signal-actor: orgs=${githubOrgs.join(', ')}, lookbackDays=${lookbackDays}`);

const octokit = new Octokit({ auth: githubToken || undefined });
const cutoff = new Date();
cutoff.setDate(cutoff.getDate() - lookbackDays);

const signals: Signal[] = [];

for (const orgInput of githubOrgs) {
  // Resolve org handle (might be company name or already a handle)
  let orgHandle = orgInput;
  if (orgInput.includes(' ') || orgInput.length > 39) {
    const resolved = await resolveGithubOrg(orgInput, githubToken);
    if (!resolved) {
      console.warn(`Could not resolve org handle for: "${orgInput}", skipping`);
      continue;
    }
    orgHandle = resolved;
  }

  console.log(`Analyzing GitHub org: ${orgHandle}`);

  // Fetch org info
  let orgData: { login: string; name?: string | null; blog?: string | null; html_url: string };
  try {
    const { data } = await octokit.orgs.get({ org: orgHandle });
    orgData = data;
  } catch (err) {
    console.error(`Error fetching org "${orgHandle}":`, err);
    continue;
  }

  const companyName = orgData.name ?? orgHandle;
  const domain = extractDomain(orgData.blog ?? '') || `${orgHandle.toLowerCase()}.com`;

  // Fetch all repos
  let repos: RepoInfo[] = [];
  try {
    const { data } = await octokit.repos.listForOrg({
      org: orgHandle,
      per_page: 100,
      sort: 'created',
      direction: 'desc',
      type: 'public',
    });
    repos = data as RepoInfo[];
  } catch (err) {
    console.error(`Error fetching repos for "${orgHandle}":`, err);
    continue;
  }

  // Detect new repos created within lookback window
  const newRepos = repos.filter((r) => {
    if (!r.created_at) return false;
    const created = new Date(r.created_at);
    return created >= cutoff && !r.fork;
  });

  if (newRepos.length > 0) {
    const repoNames = newRepos.map((r) => r.name).join(', ');
    const languages = [...new Set(newRepos.map((r) => r.language).filter(Boolean))];
    const signalDate = newRepos[0].created_at ?? new Date().toISOString();

    const id = createHash('sha256')
      .update(`${companyName}:github:new_repos:${signalDate.split('T')[0]}`)
      .digest('hex')
      .slice(0, 16);

    signals.push({
      id,
      company: companyName,
      company_domain: domain,
      signal_type: 'github',
      signal_text: `${companyName} created ${newRepos.length} new GitHub repo(s) in the last ${lookbackDays} days: ${repoNames}`,
      source_url: `https://github.com/${orgHandle}`,
      date: new Date(signalDate).toISOString(),
      raw: {
        org: orgHandle,
        new_repo_count: newRepos.length,
        new_repos: newRepos.map((r) => ({
          name: r.name,
          language: r.language,
          description: r.description,
          created_at: r.created_at,
          html_url: r.html_url,
        })),
        new_languages: languages,
      },
    });
  }

  // Detect new languages appearing in recent repos vs older repos
  const recentLanguages = new Set(
    repos
      .filter((r) => r.created_at && new Date(r.created_at) >= cutoff && !r.fork)
      .map((r) => r.language)
      .filter((l): l is string => l !== null)
  );

  const historicLanguages = new Set(
    repos
      .filter((r) => r.created_at && new Date(r.created_at) < cutoff && !r.fork)
      .map((r) => r.language)
      .filter((l): l is string => l !== null)
  );

  const newLanguages = [...recentLanguages].filter((l) => !historicLanguages.has(l));

  if (newLanguages.length > 0) {
    const signalDate = new Date().toISOString().split('T')[0];
    const id = createHash('sha256')
      .update(`${companyName}:github:stack_change:${signalDate}`)
      .digest('hex')
      .slice(0, 16);

    signals.push({
      id,
      company: companyName,
      company_domain: domain,
      signal_type: 'github',
      signal_text: `${companyName} added new tech stack languages: ${newLanguages.join(', ')} — potential new team formation`,
      source_url: `https://github.com/${orgHandle}`,
      date: new Date().toISOString(),
      raw: {
        org: orgHandle,
        new_languages: newLanguages,
        historic_languages: [...historicLanguages],
      },
    });
  }

  // Detect external contributor surge: check recently active public repos
  const activeRepos = repos.filter((r) => {
    if (!r.updated_at) return false;
    return new Date(r.updated_at) >= cutoff && !r.fork;
  }).slice(0, 5);

  let totalExternalContributors = 0;
  for (const repo of activeRepos) {
    try {
      const { data: contributors } = await octokit.repos.listContributors({
        owner: orgHandle,
        repo: repo.name,
        per_page: 50,
      });

      // External contributors: those not in org (simplified check by filtering out org members)
      const externalCount = contributors.filter(
        (c) => c.type === 'User' && !(c.login ?? '').startsWith('bot')
      ).length;
      totalExternalContributors += externalCount;
    } catch {
      // Skip repos with no contributor data
    }
  }

  if (totalExternalContributors > 20 && activeRepos.length > 0) {
    const signalDate = new Date().toISOString().split('T')[0];
    const id = createHash('sha256')
      .update(`${companyName}:github:contributors:${signalDate}`)
      .digest('hex')
      .slice(0, 16);

    signals.push({
      id,
      company: companyName,
      company_domain: domain,
      signal_type: 'github',
      signal_text: `${companyName} has ${totalExternalContributors} active contributors across ${activeRepos.length} repos — high engineering activity`,
      source_url: `https://github.com/${orgHandle}`,
      date: new Date().toISOString(),
      raw: {
        org: orgHandle,
        total_external_contributors: totalExternalContributors,
        active_repo_count: activeRepos.length,
        active_repos: activeRepos.map((r) => r.name),
      },
    });
  }

  // Rate limit: avoid hitting GitHub API limits
  await sleep(1000);
}

console.log(`Generated ${signals.length} GitHub signals`);

// Deduplicate
const seenIds = new Set<string>();
const uniqueSignals = signals.filter((s) => {
  if (seenIds.has(s.id)) return false;
  seenIds.add(s.id);
  return true;
});

// Push to dataset
await pushDataWithFallback(uniqueSignals, DATASETS.SIGNALS);

console.log(`Pushed ${uniqueSignals.length} GitHub signals to dataset '${DATASETS.SIGNALS}'`);

await Actor.exit();

function extractDomain(url: string): string {
  if (!url) return '';
  try {
    const parsed = new URL(url.startsWith('http') ? url : `https://${url}`);
    return parsed.hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
