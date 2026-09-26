// Run once after restarting the temporary local Next probe with an empty dev cache.
// Distinct location names avoid reusing the location suggestion cache slot.
import { performance } from "node:perf_hooks";

const baseUrl = process.argv[2] ?? "http://localhost:3150";
const samples = [
  ["remote nurse Geneva", "geneva"],
  ["hybrid accountant London", "london"],
  ["onsite designer Paris", "paris"],
  ["senior recruiter Munich", "munich"],
  ["junior analyst Amsterdam", "amsterdam"],
  ["staff architect Vienna", "vienna"],
  ["lead pharmacist Madrid", "madrid"],
  ["remote developer Toronto", "toronto"],
  ["hybrid scientist Milan", "milan"],
  ["onsite teacher Stockholm", "stockholm"],
  ["principal consultant Copenhagen", "copenhagen"],
  ["intern auditor Dublin", "dublin"],
];
const observations = [];
for (const [q, expectedLocation] of samples) {
  const url = new URL("/api/experiments/filter-latency", baseUrl);
  url.searchParams.set("q", q);
  const started = performance.now();
  const response = await fetch(url, { signal: AbortSignal.timeout(15_000) });
  const body = await response.json();
  const clientMs = Math.round(performance.now() - started);
  if (!response.ok || typeof body.parserMs !== "number") {
    throw new Error(`Parser route returned HTTP ${response.status}`);
  }
  const locations = body.parsed.locations.map((location) => location.slug);
  observations.push({
    q,
    expectedLocation,
    locations,
    valid: locations.some((slug) => slug === expectedLocation || slug.startsWith(`${expectedLocation}-`)),
    parserMs: body.parserMs,
    clientMs,
    otherFilters: {
      occupations: body.parsed.occupations.map((item) => item.slug),
      seniorities: body.parsed.seniorities.map((item) => item.slug),
      technologies: body.parsed.technologies.map((item) => item.slug),
      workMode: body.parsed.workMode,
    },
  });
  await new Promise((resolve) => setTimeout(resolve, 3_000));
}
const valid = observations.filter((row) => row.valid);
function percentile(values, fraction) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.ceil(fraction * sorted.length) - 1];
}
console.log(JSON.stringify({
  timestamp: new Date().toISOString(),
  samples: observations.length,
  validSamples: valid.length,
  parserMedianMs: valid.length ? percentile(valid.map((row) => row.parserMs), 0.5) : null,
  parserP95Ms: valid.length ? percentile(valid.map((row) => row.parserMs), 0.95) : null,
  observations,
}, null, 2));
