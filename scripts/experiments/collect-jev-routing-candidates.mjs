import { readFileSync } from "node:fs";

// Read the adjudicated span file, not the existing parser output. This is a
// separate check of whether the app's real Typesense services can normalize
// terms a router would send them, including misspellings and multiword places.
const [labelsPath, baseUrl = "http://localhost:3150"] = process.argv.slice(2);
if (!labelsPath) throw new Error("Usage: node collect-jev-routing-candidates.mjs <intent-gold.json> [baseUrl]");
const labelled = JSON.parse(readFileSync(labelsPath, "utf8")).records;
const categories = new Set(["location", "occupation", "seniority", "technology"]);
const seen = new Map();
async function lookup(locale, category, term) {
  const key = JSON.stringify([locale, category, term.toLowerCase()]);
  if (seen.has(key)) return { ...seen.get(key), reused: true };
  let lastError;
  for (let attempt = 0; attempt < 6; attempt++) {
    try {
      const started = performance.now();
      const response = await fetch(new URL("/api/experiments/filter-latency", baseUrl), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ locale, requests: [{ key: "term", category, text: term }] }),
        signal: AbortSignal.timeout(15_000),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.json();
      const result = { suggestions: body.results.term, latencyMs: Math.round(performance.now() - started), reused: false };
      seen.set(key, result);
      return result;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
    }
  }
  return { suggestions: [], error: String(lastError), reused: false };
}
for (const record of labelled) {
  const terms = [];
  for (const term of record.terms) {
    if (!categories.has(term.category)) continue;
    const result = await lookup(record.locale, term.category, term.text);
    terms.push({ text: term.text, category: term.category, ...result });
    await new Promise((resolve) => setTimeout(resolve, 125));
  }
  process.stdout.write(JSON.stringify({ id: record.id, split: record.split, group: record.group, locale: record.locale, q: record.q, terms }) + "\n");
}
