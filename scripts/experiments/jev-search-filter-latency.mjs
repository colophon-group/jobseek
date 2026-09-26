// Read-only latency probe for the search-input decision path.
// Run with --typesafe-env and --typesense-env pointing to local, untracked env files.
import { readFileSync } from "node:fs";
import { performance } from "node:perf_hooks";

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index < 0 ? fallback : process.argv[index + 1];
}

function envValue(path, name) {
  if (process.env[name]) return process.env[name];
  if (!path) throw new Error(`Missing ${name} and its env-file argument`);
  const line = readFileSync(path, "utf8").split(/\r?\n/)
    .find((entry) => entry.startsWith(`${name}=`));
  if (!line) throw new Error(`Missing ${name} in env file`);
  return line.slice(name.length + 1).trim().replace(/^['"]|['"]$/g, "");
}

const typeSafeEnv = arg("typesafe-env");
const typesenseEnv = arg("typesense-env");
const typesenseMode = arg("typesense-mode", "off");
if (!["on", "off"].includes(typesenseMode)) throw new Error("--typesense-mode must be on or off");
const repetitions = Number(arg("reps", "12"));
if (!Number.isSafeInteger(repetitions) || repetitions < 1 || repetitions > 100) {
  throw new Error("--reps must be an integer from 1 to 100");
}
const typeSafeToken = envValue(typeSafeEnv, "TYPESAFE_AI_TOKEN");
const typesenseKey = typesenseMode === "on" ? envValue(typesenseEnv, "TYPESENSE_SEARCH_KEY") : null;
const typesenseBase = typesenseMode === "on"
  ? `${envValue(typesenseEnv, "TYPESENSE_PROTOCOL")}://${envValue(typesenseEnv, "TYPESENSE_HOST")}:${envValue(typesenseEnv, "TYPESENSE_PORT")}`
  : null;

// Each option stands for an existing structured filter. "keyword" preserves
// free text. This is a representative decision shape, not a quality eval.
const fixtures = [
  {
    name: "short",
    query: "remote engineer",
    terms: [
      { text: "remote", choices: { keyword: "Keep as free text", remote: "Remote work mode" } },
      { text: "engineer", choices: { keyword: "Keep as free text", software_engineer: "Software Engineer occupation" } },
    ],
  },
  {
    name: "typical",
    query: "senior Python developer Zurich remote",
    terms: [
      { text: "senior", choices: { keyword: "Keep as free text", senior: "Senior seniority" } },
      { text: "Python", choices: { keyword: "Keep as free text", python: "Python technology" } },
      { text: "developer", choices: { keyword: "Keep as free text", software_engineer: "Software Engineer occupation" } },
      { text: "Zurich", choices: { keyword: "Keep as free text", zurich: "Zurich, Switzerland location" } },
      { text: "remote", choices: { keyword: "Keep as free text", remote: "Remote work mode" } },
    ],
  },
  {
    name: "long",
    query: "hybrid staff backend engineer Rust Kubernetes Berlin",
    terms: [
      { text: "hybrid", choices: { keyword: "Keep as free text", hybrid: "Hybrid work mode" } },
      { text: "staff", choices: { keyword: "Keep as free text", staff: "Staff seniority" } },
      { text: "backend", choices: { keyword: "Keep as free text", backend_developer: "Backend Developer occupation" } },
      { text: "engineer", choices: { keyword: "Keep as free text", software_engineer: "Software Engineer occupation" } },
      { text: "Rust", choices: { keyword: "Keep as free text", rust: "Rust technology" } },
      { text: "Kubernetes", choices: { keyword: "Keep as free text", kubernetes: "Kubernetes technology" } },
      { text: "Berlin", choices: { keyword: "Keep as free text", berlin: "Berlin, Germany location" } },
    ],
  },
];

function jevPayload(fixture) {
  return {
    model: "jev-1.13.0",
    state: { search_query: fixture.query, terms: fixture.terms.map(({ text }) => text) },
    questions: Object.fromEntries(fixture.terms.map((term, index) => [
      `term_${index + 1}`,
      {
        type: "choice",
        instructions: `For term ${index + 1} (${term.text}) in state.search_query, select the intended search filter or keep it as a keyword. Use the full query for context.`,
        criteria: term.choices,
      },
    ])),
  };
}

async function measureJev(fixture) {
  const payload = jevPayload(fixture);
  const start = performance.now();
  const response = await fetch("https://api.typesafe.ai/v1/systemone", {
    method: "POST",
    headers: { Authorization: `Bearer ${typeSafeToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(10_000),
  });
  const body = await response.json();
  const latencyMs = performance.now() - start;
  if (!response.ok) throw new Error(`Jev HTTP ${response.status}`);
  if (body.model !== "jev-1.13.0" || Object.keys(body.answers ?? {}).length !== fixture.terms.length) {
    throw new Error("Jev response had unexpected model or answer count");
  }
  return {
    latencyMs: Math.round(latencyMs),
    inputTokens: body.usage.input_tokens,
    outputTokens: body.usage.output_tokens,
    choices: Object.values(body.answers).map((answer) => answer.choice),
  };
}

// Mirror the current tokenizer's single, pair, and triplet fan-out and its
// four suggest* collection requests. This measures direct uncached Typesense
// transport, not the Next.js server action or its `use cache` behavior.
function candidates(query) {
  const words = query.split(/\s+/).filter(Boolean);
  const all = new Set(words);
  for (let i = 0; i < words.length - 1; i++) all.add(`${words[i]} ${words[i + 1]}`);
  if (words.length <= 10) {
    for (let i = 0; i < words.length - 2; i++) all.add(`${words[i]} ${words[i + 1]} ${words[i + 2]}`);
  }
  return { words: [...new Set(words)], occupations: [...all] };
}

function typesenseUrl(collection, query) {
  const url = new URL(`${typesenseBase}/collections/${collection}/documents/search`);
  url.searchParams.set("q", query.toLowerCase());
  url.searchParams.set("query_by", collection === "location" ? "name_en,aliases" : collection === "technology" ? "name,slug" : "name,aliases");
  url.searchParams.set("filter_by", collection === "location" || collection === "technology" ? "has_active_postings:true" : "has_active_postings:true && locale:en");
  url.searchParams.set("per_page", collection === "location" ? "8" : "5");
  url.searchParams.set("prefix", "true");
  url.searchParams.set("num_typos", collection === "technology" ? "0" : "1");
  return url;
}

async function measureTypesense(fixture) {
  const { words, occupations } = candidates(fixture.query);
  const requests = [
    ...words.flatMap((word) => ["seniority", "location", "technology"].map((collection) => [collection, word])),
    ...occupations.map((word) => ["occupation", word]),
  ];
  const start = performance.now();
  await Promise.all(requests.map(async ([collection, word]) => {
    if (word.length < 2) return;
    const response = await fetch(typesenseUrl(collection, word), {
      headers: { "X-TYPESENSE-API-KEY": typesenseKey },
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) throw new Error(`Typesense HTTP ${response.status}`);
    await response.arrayBuffer();
  }));
  return { latencyMs: Math.round(performance.now() - start), requests: requests.length };
}

function percentile(values, fraction) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.ceil(fraction * sorted.length) - 1];
}

function summary(rows, field) {
  const values = rows.map((row) => row[field]);
  return { min: Math.min(...values), median: percentile(values, 0.5), p90: percentile(values, 0.9), p95: percentile(values, 0.95), max: Math.max(...values) };
}

// Warm DNS/TLS and server-side caches once; the first result is preserved.
const warmup = { typesense: typesenseMode === "on" ? await measureTypesense(fixtures[0]) : null, jev: await measureJev(fixtures[0]) };
const observations = [];
for (let round = 0; round < repetitions; round++) {
  for (const fixture of fixtures.slice(round % fixtures.length).concat(fixtures.slice(0, round % fixtures.length))) {
    const typesense = typesenseMode === "on" ? await measureTypesense(fixture) : null;
    const jev = await measureJev(fixture);
    observations.push({ fixture: fixture.name, round: round + 1, typesense, jev, serialMs: typesense ? typesense.latencyMs + jev.latencyMs : null });
  }
}

const summaries = Object.fromEntries(fixtures.map((fixture) => {
  const rows = observations.filter((row) => row.fixture === fixture.name);
  return [fixture.name, {
    typesenseMs: typesenseMode === "on" ? summary(rows.map((row) => row.typesense), "latencyMs") : null,
    jevMs: summary(rows.map((row) => row.jev), "latencyMs"),
    serialMs: typesenseMode === "on" ? summary(rows, "serialMs") : null,
    requests: typesenseMode === "on" ? rows[0].typesense.requests : null,
    inputTokens: rows[0].jev.inputTokens,
  }];
}));
console.log(JSON.stringify({
  timestamp: new Date().toISOString(),
  location: "local macOS, Europe/Zurich",
  model: "jev-1.13.0",
  typesenseMode,
  repetitions,
  warmup,
  summaries,
  observations,
}, null, 2));
