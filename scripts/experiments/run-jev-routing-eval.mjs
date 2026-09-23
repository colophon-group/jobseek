import { readFileSync } from "node:fs";
import { performance } from "node:perf_hooks";
import { categoryRequests, jevRoutingRequest } from "./jev-routing-core.mjs";

const [variant, split, goldPath, tokenEnvPath, baseUrl = "http://localhost:3150"] = process.argv.slice(2);
if (!["natural", "literal", "minimal", "minimal2"].includes(variant) || !["tune", "holdout"].includes(split)) {
  throw new Error("Usage: node run-jev-routing-eval.mjs <natural|literal|minimal|minimal2> <tune|holdout> <current-system.json> <token.env> [baseUrl]");
}
const gold = JSON.parse(readFileSync(goldPath, "utf8"));
const line = readFileSync(tokenEnvPath, "utf8").split(/\r?\n/)
  .find((entry) => entry.startsWith("TYPESAFE_AI_TOKEN="));
if (!line) throw new Error("TYPESAFE_AI_TOKEN missing in env file");
const token = line.slice("TYPESAFE_AI_TOKEN=".length).trim().replace(/^['"]|['"]$/g, "");

async function retry(operation, label) {
  let last;
  for (let attempt = 0; attempt < 4; attempt++) {
    try { return await operation(); } catch (error) {
      last = error;
      if (attempt < 3) await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
    }
  }
  throw new Error(`${label} failed after retries`, { cause: last });
}

async function askJev(fixture) {
  const payload = jevRoutingRequest(fixture.q, fixture.locale, variant);
  const started = performance.now();
  const response = await fetch("https://api.typesafe.ai/v1/systemone", {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new Error(`Jev HTTP ${response.status}`);
  const body = await response.json();
  if (body.model !== "jev-1.13.0" || Object.keys(body.answers ?? {}).length !== Object.keys(payload.questions).length) {
    throw new Error("Jev response shape mismatch");
  }
  return { answers: body.answers, usage: body.usage, jevLatencyMs: Math.round(performance.now() - started), questionCount: Object.keys(payload.questions).length };
}

async function fetchCandidates(fixture, answers) {
  const requests = categoryRequests(fixture.q, answers);
  if (requests.length === 0) return { candidates: {}, candidateLatencyMs: 0, requestCount: 0 };
  const started = performance.now();
  const response = await fetch(new URL("/api/experiments/filter-latency", baseUrl), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ locale: fixture.locale, requests }),
    signal: AbortSignal.timeout(20_000),
  });
  if (!response.ok) throw new Error(`Candidate route HTTP ${response.status}`);
  const body = await response.json();
  if (Object.keys(body.results ?? {}).length !== requests.length) throw new Error("Candidate result count mismatch");
  return { candidates: body.results, candidateLatencyMs: Math.round(performance.now() - started), requestCount: requests.length };
}

const records = [];
for (const fixture of gold.records.filter((record) => record.split === split)) {
  const jev = await retry(() => askJev(fixture), `${fixture.id} Jev`);
  const normalized = await retry(() => fetchCandidates(fixture, jev.answers), `${fixture.id} candidates`);
  records.push({
    id: fixture.id,
    group: fixture.group,
    split,
    q: fixture.q,
    locale: fixture.locale,
    goldShape: fixture.goldShape,
    ...jev,
    ...normalized,
  });
  await new Promise((resolve) => setTimeout(resolve, 150));
}
console.log(JSON.stringify({
  timestamp: new Date().toISOString(),
  model: "jev-1.13.0",
  variant,
  split,
  count: records.length,
  records,
}, null, 2));
