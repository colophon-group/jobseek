/**
 * @actor github-signal-actor
 *
 * Analyzes GitHub org activity to detect engineering growth signals:
 *   1. New repos — rapid creation = new product lines or teams
 *   2. Stack change — new languages appearing = new infra team
 *   3. Contributor surge — high external activity = OSS/platform hiring
 *
 * Signal type: `github`
 */

import { Octokit } from '@octokit/rest';
import { runSignalActor } from '../../../shared/signalActor';
import { signalId } from '../../../shared/id';
import { extractDomain, sleep } from '../../../shared/utils';
import type { Signal } from '../../../shared/types';
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
  forks_count: number;
  stargazers_count: number;
  html_url: string;
  description: string | null;
  fork: boolean;
}

runSignalActor<GithubSignalInput>(async (input) => {
  const { githubOrgs = [], githubToken = '', lookbackDays = 14 } = input;

  if (githubOrgs.length === 0) {
    console.warn('No githubOrgs provided.');
    return [];
  }

  console.log(`github-signal-actor: orgs=${githubOrgs.join(', ')}, lookbackDays=${lookbackDays}`);

  const octokit = new Octokit({ auth: githubToken || undefined });
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - lookbackDays);

  const signals: Signal[] = [];

  for (const orgInput of githubOrgs) {
    let orgHandle = orgInput;
    if (orgInput.includes(' ') || orgInput.length > 39) {
      const resolved = await resolveGithubOrg(orgInput, githubToken);
      if (!resolved) {
        console.warn(`Could not resolve org: "${orgInput}", skipping`);
        continue;
      }
      orgHandle = resolved;
    }

    console.log(`Analyzing org: ${orgHandle}`);

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

    // Signal 1: New repos
    const newRepos = repos.filter((r) => r.created_at && new Date(r.created_at) >= cutoff && !r.fork);
    if (newRepos.length > 0) {
      const repoNames = newRepos.map((r) => r.name).join(', ');
      const languages = [...new Set(newRepos.map((r) => r.language).filter(Boolean))];
      const signalDate = newRepos[0].created_at ?? new Date().toISOString();

      signals.push({
        id: signalId(companyName, 'github', 'new_repos', signalDate.split('T')[0]),
        company: companyName,
        company_domain: domain,
        signal_type: 'github',
        signal_text: `${companyName} created ${newRepos.length} new repo(s) in ${lookbackDays} days: ${repoNames}`,
        source_url: `https://github.com/${orgHandle}`,
        date: new Date(signalDate).toISOString(),
        raw: {
          org: orgHandle,
          new_repo_count: newRepos.length,
          new_repos: newRepos.map((r) => ({
            name: r.name, language: r.language, description: r.description,
            created_at: r.created_at, html_url: r.html_url,
          })),
          new_languages: languages,
        },
      });
    }

    // Signal 2: Stack change (new languages in recent vs historic repos)
    const recentLangs = new Set(
      repos.filter((r) => r.created_at && new Date(r.created_at) >= cutoff && !r.fork)
        .map((r) => r.language).filter((l): l is string => l !== null)
    );
    const historicLangs = new Set(
      repos.filter((r) => r.created_at && new Date(r.created_at) < cutoff && !r.fork)
        .map((r) => r.language).filter((l): l is string => l !== null)
    );
    const newLangs = [...recentLangs].filter((l) => !historicLangs.has(l));

    if (newLangs.length > 0) {
      const today = new Date().toISOString().split('T')[0];
      signals.push({
        id: signalId(companyName, 'github', 'stack_change', today),
        company: companyName,
        company_domain: domain,
        signal_type: 'github',
        signal_text: `${companyName} added new stack languages: ${newLangs.join(', ')}`,
        source_url: `https://github.com/${orgHandle}`,
        date: new Date().toISOString(),
        raw: { org: orgHandle, new_languages: newLangs, historic_languages: [...historicLangs] },
      });
    }

    // Signal 3: External contributor surge
    const activeRepos = repos
      .filter((r) => r.updated_at && new Date(r.updated_at) >= cutoff && !r.fork)
      .slice(0, 5);

    let totalExternal = 0;
    for (const repo of activeRepos) {
      try {
        const { data: contributors } = await octokit.repos.listContributors({
          owner: orgHandle, repo: repo.name, per_page: 50,
        });
        totalExternal += contributors.filter(
          (c) => c.type === 'User' && !(c.login ?? '').startsWith('bot')
        ).length;
      } catch { /* skip */ }
    }

    if (totalExternal > 20 && activeRepos.length > 0) {
      const today = new Date().toISOString().split('T')[0];
      signals.push({
        id: signalId(companyName, 'github', 'contributors', today),
        company: companyName,
        company_domain: domain,
        signal_type: 'github',
        signal_text: `${companyName} has ${totalExternal} active contributors across ${activeRepos.length} repos`,
        source_url: `https://github.com/${orgHandle}`,
        date: new Date().toISOString(),
        raw: {
          org: orgHandle,
          total_external_contributors: totalExternal,
          active_repo_count: activeRepos.length,
          active_repos: activeRepos.map((r) => r.name),
        },
      });
    }

    await sleep(1000);
  }

  return signals;
});
