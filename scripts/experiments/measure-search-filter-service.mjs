// Measure the actual parseSearchFilters service via a temporary local Next route.
// Start `next dev -p 3150` with app/api/experiments/filter-latency/route.ts installed.
// This script never reads credentials or provider response bodies beyond parsed filters.
import { performance } from "node:perf_hooks";

const baseUrl = process.argv[2] ?? "http://localhost:3150";
const repetitions = Number(process.argv[3] ?? "12");
const fixtures = [
  {
    name: "short",
    q: "remote engineer",
    expected: { workMode: ["remote"] },
  },
  {
    name: "typical",
    q: "senior Python developer Zurich remote",
    expected: {
      workMode: ["remote"], locations: ["zurich"],
      occupations: ["software-engineer"], seniorities: ["senior"], technologies: ["python"],
    },
  },
  {
    name: "long",
    q: "hybrid staff backend engineer Rust Kubernetes Berlin",
    expected: {
      workMode: ["hybrid"], locations: ["berlin"],
      occupations: ["backend-developer"], seniorities: ["staff"],
      technologies: ["rust", "kubernetes"],
    },
  },
];

function labels(parsed, field) {
  return field === "workMode" ? parsed.workMode : parsed[field].map((item) => item.slug);
}

async function measure(fixture, phase, round) {
  const url = new URL("/api/experiments/filter-latency", baseUrl);
  url.searchParams.set("q", fixture.q);
  const started = performance.now();
  const response = await fetch(url, { signal: AbortSignal.timeout(15_000) });
  const body = await response.json();
  const clientMs = Math.round(performance.now() - started);
  if (!response.ok || typeof body.parserMs !== "number") {
    throw new Error(`Parser route returned HTTP ${response.status}`);
  }
  const missing = Object.entries(fixture.expected).flatMap(([field, wanted]) =>
    wanted.filter((label) => !labels(body.parsed, field).includes(label))
      .map((label) => `${field}:${label}`));
  return {
    fixture: fixture.name,
    phase,
    round,
    parserMs: body.parserMs,
    clientMs,
    valid: missing.length === 0,
    missing,
    filters: Object.fromEntries(
      ["locations", "occupations", "seniorities", "technologies", "workMode"]
        .map((field) => [field, labels(body.parsed, field)])),
  };
}

function percentile(values, fraction) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.ceil(sorted.length * fraction) - 1];
}

const observations = [];
for (const fixture of fixtures) {
  observations.push(await measure(fixture, "first", 0));
  await new Promise((resolve) => setTimeout(resolve, 1_000));
}
for (let round = 1; round <= repetitions; round++) {
  const rotated = fixtures.slice(round % fixtures.length).concat(fixtures.slice(0, round % fixtures.length));
  for (const fixture of rotated) observations.push(await measure(fixture, "repeat", round));
}
const summaries = Object.fromEntries(fixtures.map((fixture) => {
  const repeated = observations.filter((row) => row.fixture === fixture.name && row.phase === "repeat");
  return [fixture.name, {
    first: observations.find((row) => row.fixture === fixture.name && row.phase === "first"),
    repeats: repeated.length,
    parserMedianMs: percentile(repeated.map((row) => row.parserMs), 0.5),
    parserP95Ms: percentile(repeated.map((row) => row.parserMs), 0.95),
    clientMedianMs: percentile(repeated.map((row) => row.clientMs), 0.5),
    clientP95Ms: percentile(repeated.map((row) => row.clientMs), 0.95),
    allValid: repeated.every((row) => row.valid),
  }];
}));
console.log(JSON.stringify({ timestamp: new Date().toISOString(), repetitions, summaries, observations }, null, 2));
