import { readFileSync } from "node:fs";
import { performance } from "node:perf_hooks";

const [datasetPath, baseUrl = "http://localhost:3160"] = process.argv.slice(2);
if (!datasetPath) throw new Error("Usage: node collect-jev-discard-current.mjs <gold.json> [base-url]");
const dataset = JSON.parse(readFileSync(datasetPath, "utf8"));
const records = [];
for (const row of dataset.records) {
  const url = new URL("/api/experiments/filter-latency", baseUrl);
  url.searchParams.set("q", row.q);
  url.searchParams.set("locale", row.locale);
  const started = performance.now();
  const response = await fetch(url, { signal: AbortSignal.timeout(20_000) });
  if (!response.ok) throw new Error(`${row.id}: parser returned HTTP ${response.status}`);
  const body = await response.json();
  const parsed = body.parsed;
  records.push({
    id: row.id, split: row.split, group: row.group, query: row.q, locale: row.locale,
    parserMs: body.parserMs, wallMs: Math.round(performance.now() - started),
    answer: {
      keywords: parsed.keywords,
      locations: parsed.locations.map((item) => item.slug),
      occupations: parsed.occupations.map((item) => item.slug),
      seniorities: parsed.seniorities.map((item) => item.slug),
      technologies: parsed.technologies.map((item) => item.slug),
      workMode: parsed.workMode,
      employmentTypes: parsed.employmentTypes,
    },
  });
}
console.log(JSON.stringify({ timestamp: new Date().toISOString(), dataset: datasetPath,
  labelRole: "current-system-baseline-not-human-gold", baseCommit: "eb40e2b2a",
  note: "Existing parseSearchFilters through temporary local Next.js route; route removed after collection",
  count: records.length, records }, null, 2));
