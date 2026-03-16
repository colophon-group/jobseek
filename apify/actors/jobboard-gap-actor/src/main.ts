/**
 * @actor jobboard-gap-actor
 *
 * Detects stealth hiring signals by comparing a target company's open roles
 * against what its peer companies are currently hiring for.
 *
 * Logic:
 *   If 5 peer companies are all posting "Data Engineer" roles but the target
 *   has zero data engineering openings, that's a gap signal — the target is
 *   lagging behind its peers in building that capability. Speculative outreach
 *   at this point arrives *before* the req is approved.
 *
 * Signal type produced: `job_gap`
 *
 * Pipeline:
 *   1. Calls `apify/linkedin-jobs-scraper` for all target + peer companies
 *   2. classifyDept() (from gapDetector.ts) maps each job title to a dept bucket
 *   3. detectGaps() computes where target has 0 openings but peers average >2
 *   4. Each gap becomes a Signal with the computed signal_strength score
 *
 * Input schema (actor.json):
 * {
 *   targetCompanies:  string[]  (companies to monitor for gaps)
 *   peerCompanies:    string[]  (comparable companies used as the baseline)
 *   linkedinCookies:  string    (li_at cookie value — optional but improves results)
 * }
 *
 * Requires: Apify account with access to `apify/linkedin-jobs-scraper` (paid actor).
 *
 * Tip: Good peer selection = similar stage, vertical, and team size.
 *   e.g., for target = "Notion", good peers = ["Coda", "Confluence", "Airtable", "Linear"]
 */

import { Actor } from 'apify';
import { createHash } from 'crypto';
import { Signal } from '../../../shared/types';
import { DATASETS } from '../../../shared/constants';
import { pushDataWithFallback } from '../../../shared/storage';
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

await Actor.init();

const input = (await Actor.getInput<JobboardGapInput>()) ?? {};
const { targetCompanies = [], peerCompanies = [], linkedinCookies = '' } = input;

if (targetCompanies.length === 0) {
  console.warn('No targetCompanies provided. Exiting.');
  await Actor.exit();
  process.exit(0);
}

if (peerCompanies.length === 0) {
  console.warn('No peerCompanies provided — cannot compute gaps without peers. Exiting.');
  await Actor.exit();
  process.exit(0);
}

console.log(
  `Starting jobboard-gap-actor: targets=${targetCompanies.join(', ')}, peers=${peerCompanies.join(', ')}`
);

const allCompanies = [...targetCompanies, ...peerCompanies];

// Scrape LinkedIn jobs for all companies
let allJobs: LinkedinJobResult[] = [];
try {
  const scrapeInput: Record<string, unknown> = {
    queries: allCompanies.map((company) => ({
      query: company,
      location: 'Worldwide',
    })),
    resultsLimit: 200,
  };

  if (linkedinCookies) {
    scrapeInput['cookie'] = [{ name: 'li_at', value: linkedinCookies }];
  }

  console.log('Calling apify/linkedin-jobs-scraper...');
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

// Group jobs by company and department
const companyDeptMap = new Map<string, Record<string, number>>();

// Initialize all companies with empty dept maps
for (const company of allCompanies) {
  companyDeptMap.set(company.toLowerCase(), {});
}

for (const job of allJobs) {
  const jobTitle = job.title ?? job.jobTitle ?? '';
  const jobCompany = job.companyName ?? job.company ?? '';

  if (!jobTitle || !jobCompany) continue;

  const dept = classifyDept(jobTitle);

  // Match job to a tracked company
  const matchedCompany = allCompanies.find((c) =>
    jobCompany.toLowerCase().includes(c.toLowerCase()) ||
    c.toLowerCase().includes(jobCompany.toLowerCase())
  );

  if (!matchedCompany) continue;

  const key = matchedCompany.toLowerCase();
  const deptMap = companyDeptMap.get(key) ?? {};
  deptMap[dept] = (deptMap[dept] ?? 0) + 1;
  companyDeptMap.set(key, deptMap);
}

// Build DeptBreakdown objects
const peerBreakdowns: DeptBreakdown[] = peerCompanies.map((company) => ({
  company,
  departments: companyDeptMap.get(company.toLowerCase()) ?? {},
}));

const signals: Signal[] = [];

// Analyze each target company
for (const targetCompany of targetCompanies) {
  const targetBreakdown: DeptBreakdown = {
    company: targetCompany,
    departments: companyDeptMap.get(targetCompany.toLowerCase()) ?? {},
  };

  const gaps = detectGaps(targetBreakdown, peerBreakdowns);

  console.log(`Found ${gaps.length} hiring gaps for ${targetCompany}`);

  for (const gap of gaps) {
    const signalDate = new Date().toISOString().split('T')[0];
    const id = createHash('sha256')
      .update(`${targetCompany}:job_gap:${gap.dept}:${signalDate}`)
      .digest('hex')
      .slice(0, 16);

    const peerList = peerCompanies
      .filter((p) => (peerBreakdowns.find((pb) => pb.company === p)?.departments[gap.dept] ?? 0) > 0)
      .join(', ');

    const signalText =
      `${targetCompany} has 0 open ${gap.dept} roles, but peers (${peerList}) ` +
      `average ${gap.peerAvg} openings — potential stealth hiring signal`;

    const domain = guessDomainFromCompany(targetCompany);

    signals.push({
      id,
      company: targetCompany,
      company_domain: domain,
      signal_type: 'job_gap',
      signal_text: signalText,
      source_url: `https://www.linkedin.com/jobs/search/?keywords=${encodeURIComponent(targetCompany)}`,
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

console.log(`Generated ${signals.length} job gap signals`);

// Push to dataset
await pushDataWithFallback(signals, DATASETS.SIGNALS);

console.log(`Pushed ${signals.length} job gap signals to dataset '${DATASETS.SIGNALS}'`);

await Actor.exit();

function guessDomainFromCompany(company: string): string {
  const slug = company
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '')
    .slice(0, 30);
  return `${slug}.com`;
}
