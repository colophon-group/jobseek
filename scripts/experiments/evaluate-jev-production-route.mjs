import { readFileSync } from "node:fs";
import { performance } from "node:perf_hooks";

const [datasetPath, baseUrl = "http://localhost:3160"] = process.argv.slice(2);
if (!datasetPath) throw new Error("Usage: node evaluate-jev-production-route.mjs <gold.json> [base-url]");
const dataset = JSON.parse(readFileSync(datasetPath, "utf8"));
const rows = dataset.records.filter((record) => record.split === "holdout");
const records = [];
for (const [index, row] of rows.entries()) {
  // The production route enforces 12 requests/minute per IP. Exercise it
  // without bypassing its limiter so this remains an end-to-end check.
  if (index === 10) await new Promise((resolve) => setTimeout(resolve, 65_000));
  const started = performance.now();
  const response = await fetch(new URL("/api/search/query-intent", baseUrl), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query: row.q, locale: row.locale }),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(`${row.id}: route returned HTTP ${response.status} (${body.error})`);
  records.push({
    id: row.id, query: row.q, locale: row.locale, intent: body.intent,
    wallMs: Math.round(performance.now() - started),
    serverTiming: response.headers.get("server-timing"),
    keywords: body.keywords,
    locations: body.locations.map((item) => item.slug),
    occupations: body.occupations.map((item) => item.slug),
    seniorities: body.seniorities.map((item) => item.slug),
    technologies: body.technologies.map((item) => item.slug),
    workMode: body.workMode,
    employmentTypes: body.employmentTypes,
    unresolved: body.terms.filter((term) => ["ambiguous", "unresolved"].includes(term.status))
      .map((term) => ({ text: term.span.text, category: term.span.category, status: term.status,
        alternatives: term.alternatives.map((item) => item.slug) })),
  });
}
console.log(JSON.stringify({ timestamp: new Date().toISOString(), dataset: datasetPath,
  note: "Post-hoc production-route audit on frozen span labels, not a blind complete-configuration evaluation",
  count: records.length, records }, null, 2));
