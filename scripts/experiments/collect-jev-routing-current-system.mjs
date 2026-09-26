import { performance } from "node:perf_hooks";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const baseUrl = process.argv[2] ?? "http://localhost:3150";
const fixturesPath = process.argv[3] ?? "scripts/experiments/jev-routing-fixtures.mjs";
const { routingFixtures } = await import(pathToFileURL(resolve(fixturesPath)).href);

function scoreShape(parsed) {
  return {
    keywords: parsed.keywords,
    locations: parsed.locations.map((item) => item.slug),
    occupations: parsed.occupations.map((item) => item.slug),
    seniorities: parsed.seniorities.map((item) => item.slug),
    technologies: parsed.technologies.map((item) => item.slug),
    workMode: parsed.workMode,
    employmentTypes: parsed.employmentTypes,
  };
}

async function query(fixture) {
  const url = new URL("/api/experiments/filter-latency", baseUrl);
  url.searchParams.set("q", fixture.q);
  url.searchParams.set("locale", fixture.locale);
  const started = performance.now();
  const response = await fetch(url, { signal: AbortSignal.timeout(20_000) });
  const body = await response.json();
  if (!response.ok || typeof body.parserMs !== "number" || !body.parsed) {
    throw new Error(`${fixture.id}: parser route HTTP ${response.status}`);
  }
  return {
    output: body.parsed,
    shape: scoreShape(body.parsed),
    parserMs: body.parserMs,
    clientMs: Math.round(performance.now() - started),
  };
}

const sanity = {
  "atomic-01": ["workMode", "remote"],
  "atomic-02": ["workMode", "hybrid"],
  "atomic-03": ["workMode", "onsite"],
  "atomic-04": ["seniorities", "senior"],
  "atomic-07": ["technologies", "python"],
  "atomic-09": ["locations", "zurich"],
  "diverse-atomic-01": ["occupations", "accountant"],
  "diverse-atomic-02": ["occupations", "pharmacist"],
  "diverse-atomic-03": ["occupations", "customer-success-manager"],
  "diverse-atomic-04": ["occupations", "warehouse-associate"],
  // This captures the current parser's incorrect literal location match.
  "diverse-atomic-05": ["locations", "officer"],
  "diverse-atomic-06": ["occupations", "mechanical-engineer"],
  "diverse-atomic-07": ["occupations", "recruiter"],
  "diverse-atomic-08": ["occupations", "supply-chain-manager"],
};

const records = [];
for (const fixture of routingFixtures) {
  let previous = null;
  let stable = false;
  const attempts = [];
  for (let index = 0; index < 4; index++) {
    const result = await query(fixture);
    attempts.push({ parserMs: result.parserMs, clientMs: result.clientMs, shape: result.shape });
    if (previous && JSON.stringify(result.shape) === JSON.stringify(previous.shape)) {
      stable = true;
      previous = result;
      break;
    }
    previous = result;
    await new Promise((resolve) => setTimeout(resolve, index === 0 ? 300 : 1_000));
  }
  const check = sanity[fixture.id];
  const sanityPassed = !check || previous.shape[check[0]].some((value) => value === check[1] || value.startsWith(`${check[1]}-`));
  records.push({
    ...fixture,
    gold: previous.output,
    goldShape: previous.shape,
    stable,
    sanityPassed,
    attempts,
  });
  await new Promise((resolve) => setTimeout(resolve, 350));
}

console.log(JSON.stringify({
  timestamp: new Date().toISOString(),
  labelRole: "current-system-baseline-not-human-gold",
  source: "apps/web/src/lib/services/search-input.ts via local Next.js runtime",
  baseCommit: "eb40e2b2a",
  fixtureCount: routingFixtures.length,
  stableCount: records.filter((record) => record.stable).length,
  sanityPassedCount: records.filter((record) => record.sanityPassed).length,
  records,
}, null, 2));
