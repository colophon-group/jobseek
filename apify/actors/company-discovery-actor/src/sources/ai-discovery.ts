/**
 * AI-powered portal discovery using Google Gemini.
 *
 * Given the current portal registry, asks Gemini to:
 *   1. Analyse what types of sources we already cover
 *   2. Identify gaps (geographies, industries, ATS platforms)
 *   3. Suggest 3-6 new job boards/APIs with concrete scraping strategies
 *
 * Returns candidate PortalDefinition[] ready to be probed.
 */
import { log } from 'apify';
import { GoogleGenerativeAI } from '@google/generative-ai';
import type { PortalDefinition, PortalRegistry } from '../types.js';

export async function suggestNewPortals(
  registry: PortalRegistry,
  apiKey: string,
): Promise<PortalDefinition[]> {
  const genAI = new GoogleGenerativeAI(apiKey);
  const model = genAI.getGenerativeModel({ model: 'gemini-2.5-flash' });

  const activePortals = registry.portals.filter(p => p.status === 'active').map(p => ({
    id: p.id, name: p.name, companiesFound: p.companiesFound ?? 0, description: p.description,
  }));
  const failedPortalIds = registry.portals.filter(p => p.status === 'failed').map(p => p.id);
  // Active portals returning very few companies may have coverage gaps
  const lowYieldPortals = activePortals.filter(p => p.companiesFound < 5).map(p => p.id);

  // Static sources are always run — must be excluded from suggestions
  const STATIC_SOURCES = [
    'greenhouse', 'greenhouse-cdx', 'themuse', 'arbeitnow', 'remotive', 'remoteok', 'megaemployers',
    'hiring-cafe', 'himalayas', 'ycombinator', 'ashby', 'ashby-boards', 'lever', 'workable', 'bamboohr',
    'recruitee', 'jazzhr', 'breezyhr', 'icims', 'taleo', 'teamtailor', 'personio',
    'jobvite', 'successfactors', 'smartrecruiters', 'pinpoint', 'comeet', 'fountain', 'rippling',
    'linkedin', 'indeed', 'glassdoor', 'stepstone', 'xing', 'workday-cdx',
    'wellfound', 'weworkremotely', 'softgarden', 'join',
  ];

  const prompt = `You are an expert on job boards, ATS (applicant tracking systems), and job aggregator APIs.

We are building a job company discovery system. We already cover these ${STATIC_SOURCES.length} static sources (do NOT suggest any of them):
${STATIC_SOURCES.join(', ')}

We also have these ACTIVE dynamically-discovered portals:
${JSON.stringify(activePortals, null, 2)}

These portal IDs have already been tried and FAILED — do NOT suggest them again:
${JSON.stringify(failedPortalIds)}
${lowYieldPortals.length > 0 ? `\nThese active portals returned very few companies (<5) — they may need a different strategy: ${JSON.stringify(lowYieldPortals)}` : ''}

Your task: suggest 4 NEW job portals or ATS systems we are NOT yet scraping and NOT in the failed list.
Focus on:
- Public APIs that return JSON (no login required)
- ATS platforms with public company job boards (like Greenhouse/Lever pattern)
- Job aggregators covering geographies we're missing (Asia-Pacific, LATAM, Middle East, Africa, Nordics, Switzerland)
- Swiss/EU-specific job boards: jobs.ch, jobup.ch, jobscout24.ch, swissdevjobs.ch, eurojobs.com, finn.no, jobnet.dk, mol.fi (Finnish job board)
- Niche boards (healthcare/biotech, finance, defense, ESG/climate, diversity-focused, watchmaking/luxury)
- Emerging ATS platforms growing in market share (Workstream, Paradox, Occupop, Factorial, Recruitly, Manatal, Breezy, Zoho Recruit, Recooty)
- Company career APIs built on AI-native HR platforms

For each suggestion, provide a complete scraping strategy. You MUST return valid JSON only — no markdown, no explanation outside the JSON.

Return this exact structure:
{
  "reasoning": "1-2 sentence analysis of what gaps exist in our current coverage",
  "suggestions": [
    {
      "id": "lever",
      "name": "Lever ATS",
      "description": "Company-based ATS — each company has public job postings at jobs.lever.co/{company}",
      "homepageUrl": "https://lever.co",
      "strategy": {
        "type": "company_probe",
        "urlTemplate": "https://api.lever.co/v0/postings/{company}?mode=json",
        "seedCompanies": ["netflix", "stripe", "airbnb", "github", "notion", "figma", "discord"],
        "jobsArrayPath": "",
        "companyField": "",
        "countField": ""
      },
      "geminiReasoning": "Lever is used by thousands of tech companies and has a fully public API"
    }
  ]
}

Rules:
- type must be one of: json_api, company_probe, paginated_api, html_scrape
- For paginated_api: urlTemplate must contain {page} placeholder
- For company_probe: urlTemplate must contain {company} placeholder, seedCompanies must have 5+ entries
- For json_api: urlTemplate is a direct URL, jobsArrayPath is the dot-path to the array
- Only suggest portals with genuinely public, unauthenticated APIs
- Do not suggest portals already in our list
- IDs must be lowercase snake_case`;

  log.info('Asking Gemini to suggest new job portals...');

  let text = '';
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      const result = await model.generateContent(prompt);
      text = result.response.text().trim();
      break;
    } catch (err: unknown) {
      const msg = String(err);
      const retryMatch = msg.match(/retry in ([\d.]+)s/);
      const minWaitMs = 12_000; // 5 RPM = 1 req per 12s
      const waitMs = Math.max(minWaitMs, retryMatch ? Math.ceil(parseFloat(retryMatch[1])) * 1000 + 500 : 5000 * (attempt + 1));
      if (attempt < 3 && (msg.includes('429') || msg.includes('quota'))) {
        log.warning(`Gemini rate limited, retrying in ${waitMs}ms (attempt ${attempt + 1})`);
        await new Promise(r => setTimeout(r, waitMs));
      } else {
        throw err;
      }
    }
  }
  if (!text) throw new Error('Gemini returned empty response after retries');

  // Strip markdown code fences if present
  const jsonText = text.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '').trim();

  let parsed: { reasoning: string; suggestions: Partial<PortalDefinition>[] };
  try {
    parsed = JSON.parse(jsonText);
  } catch (err) {
    log.error('Gemini returned invalid JSON', { text: text.slice(0, 500) });
    throw new Error(`Gemini JSON parse failed: ${err}`);
  }

  log.info(`Gemini reasoning: ${parsed.reasoning}`);
  log.info(`Gemini suggested ${parsed.suggestions.length} new portals`);

  const now = new Date().toISOString();
  const candidates: PortalDefinition[] = [];

  for (const s of parsed.suggestions) {
    if (!s.id || !s.name || !s.strategy) {
      log.warning(`Skipping incomplete suggestion: ${JSON.stringify(s)}`);
      continue;
    }

    // Skip if already in registry
    if (registry.portals.find(p => p.id === s.id)) {
      log.info(`Skipping already-known portal: ${s.id}`);
      continue;
    }

    candidates.push({
      id: s.id,
      name: s.name,
      description: s.description ?? '',
      homepageUrl: s.homepageUrl ?? '',
      strategy: s.strategy!,
      status: 'candidate',
      suggestedBy: 'gemini',
      geminiReasoning: s.geminiReasoning ?? parsed.reasoning,
      discoveredAt: now,
    });
  }

  return candidates;
}
