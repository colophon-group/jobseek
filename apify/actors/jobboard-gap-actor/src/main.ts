/**
 * @actor jobboard-gap-actor
 *
 * Detects stealth hiring signals by comparing target company's open roles
 * against peer companies. If peers are all hiring in a department but the
 * target has zero openings, that's a gap signal.
 * Signal type: `job_gap`
 */

import { Actor } from 'apify';
import { runSignalActor } from '../../../shared/signalActor';
import { signalId } from '../../../shared/id';
import { guessDomain } from '../../../shared/utils';
import type { Signal } from '../../../shared/types';
import { detectGaps, classifyDept, DeptBreakdown } from './gapDetector';

interface JobboardGapInput {
  targetCompanies?: string[];
  peerCompanies?: string[];
  linkedinCookies?: string;
}

interface LinkedinJobResult {
  title?: string;
  jobTitle?: string;
  companyName?: string;
  company?: string;
  location?: string;
  url?: string;
  jobUrl?: string;
  postedAt?: string;
}

runSignalActor<JobboardGapInput>(async (input) => {
  const { targetCompanies = [], peerCompanies = [], linkedinCookies = '' } = input;

  if (targetCompanies.length === 0) {
    console.warn('No targetCompanies provided.');
    return [];
  }
  if (peerCompanies.length === 0) {
    console.warn('No peerCompanies — cannot compute gaps.');
    return [];
  }

  console.log(`jobboard-gap-actor: targets=${targetCompanies.join(', ')}, peers=${peerCompanies.join(', ')}`);

  const allCompanies = [...targetCompanies, ...peerCompanies];

  // Scrape LinkedIn jobs
  let allJobs: LinkedinJobResult[] = [];
  try {
    const scrapeInput: Record<string, unknown> = {
      queries: allCompanies.map((company) => ({ query: company, location: 'Worldwide' })),
      resultsLimit: 200,
    };
    if (linkedinCookies) scrapeInput['cookie'] = [{ name: 'li_at', value: linkedinCookies }];

    const run = await Actor.call('apify/linkedin-jobs-scraper', scrapeInput);
    if (run?.defaultDatasetId) {
      const dataset = await Actor.openDataset(run.defaultDatasetId);
      const { items } = await dataset.getData();
      allJobs = items as LinkedinJobResult[];
      console.log(`Retrieved ${allJobs.length} job postings`);
    }
  } catch (err) {
    console.error('Error calling apify/linkedin-jobs-scraper:', err);
  }

  // Group jobs by company + department
  const companyDeptMap = new Map<string, Record<string, number>>();
  for (const company of allCompanies) companyDeptMap.set(company.toLowerCase(), {});

  for (const job of allJobs) {
    const jobTitle = job.title ?? job.jobTitle ?? '';
    const jobCompany = job.companyName ?? job.company ?? '';
    if (!jobTitle || !jobCompany) continue;

    const dept = classifyDept(jobTitle);
    const matchedCompany = allCompanies.find(
      (c) => jobCompany.toLowerCase().includes(c.toLowerCase()) || c.toLowerCase().includes(jobCompany.toLowerCase())
    );
    if (!matchedCompany) continue;

    const key = matchedCompany.toLowerCase();
    const deptMap = companyDeptMap.get(key) ?? {};
    deptMap[dept] = (deptMap[dept] ?? 0) + 1;
    companyDeptMap.set(key, deptMap);
  }

  const peerBreakdowns: DeptBreakdown[] = peerCompanies.map((company) => ({
    company,
    departments: companyDeptMap.get(company.toLowerCase()) ?? {},
  }));

  // Detect gaps for each target
  const signals: Signal[] = [];

  for (const target of targetCompanies) {
    const targetBreakdown: DeptBreakdown = {
      company: target,
      departments: companyDeptMap.get(target.toLowerCase()) ?? {},
    };

    const gaps = detectGaps(targetBreakdown, peerBreakdowns);
    console.log(`Found ${gaps.length} hiring gaps for ${target}`);

    for (const gap of gaps) {
      const today = new Date().toISOString().split('T')[0];
      const peerList = peerCompanies
        .filter((p) => (peerBreakdowns.find((pb) => pb.company === p)?.departments[gap.dept] ?? 0) > 0)
        .join(', ');

      signals.push({
        id: signalId(target, 'job_gap', gap.dept, today),
        company: target,
        company_domain: guessDomain(target),
        signal_type: 'job_gap',
        signal_text: `${target} has 0 open ${gap.dept} roles, but peers (${peerList}) average ${gap.peerAvg} openings`,
        source_url: `https://www.linkedin.com/jobs/search/?keywords=${encodeURIComponent(target)}`,
        date: new Date().toISOString(),
        score: gap.signal_strength,
        raw: {
          department: gap.dept,
          target_count: gap.targetCount,
          peer_avg: gap.peerAvg,
          signal_strength: gap.signal_strength,
          peers_hiring: peerList,
          target_breakdown: targetBreakdown.departments,
        },
      });
    }
  }

  return signals;
});
