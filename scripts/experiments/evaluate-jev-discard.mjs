import { readFileSync } from "node:fs";
import { performance } from "node:perf_hooks";
import { jevRoutingRequest, selectedRouteSpans, tokenize } from "./jev-routing-core.mjs";

const [datasetPath, tokenEnvPath, variant = "catalog6", split = "all"] = process.argv.slice(2);
if (!datasetPath || !tokenEnvPath) throw new Error("Usage: node evaluate-jev-discard.mjs <dataset.json> <token.env> [variant] [split]");
const dataset = JSON.parse(readFileSync(datasetPath, "utf8"));
const tokenLine = readFileSync(tokenEnvPath, "utf8").split(/\r?\n/)
  .find((line) => line.startsWith("TYPESAFE_AI_TOKEN="));
if (!tokenLine) throw new Error("TYPESAFE_AI_TOKEN missing");
const token = tokenLine.slice("TYPESAFE_AI_TOKEN=".length).trim().replace(/^['"]|['"]$/g, "");

function canonical(spans) {
  return spans.filter((span) => span.category !== "discard")
    .map(({ text, category, segment, start, end }) => ({ text, category, segment, start, end }))
    .sort((a, b) => a.segment - b.segment || a.start - b.start || a.end - b.end);
}

function coverage(query, spans) {
  const segments = tokenize(query);
  const marked = segments.map((words) => words.map(() => false));
  for (const span of spans.filter((item) => item.category === "discard")) {
    for (let i = span.start; i < span.end; i++) marked[span.segment][i] = true;
  }
  return marked.flatMap((words, segment) => words.flatMap((used, index) => used ? [`${segment}:${index}`] : []));
}

const records = [];
for (const item of dataset.records.filter((row) => split === "all" || row.split === split)) {
  const request = jevRoutingRequest(item.q, item.locale, variant);
  const started = performance.now();
  const response = await fetch("https://api.typesafe.ai/v1/systemone", {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify(request),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`${item.id}: Jev HTTP ${response.status}`);
  const body = await response.json();
  if (body.model !== request.model) throw new Error(`${item.id}: unexpected model`);
  const intent = variant === "catalog7" ? body.answers?.intent?.choice : "jobSearch";
  const selected = selectedRouteSpans(item.q, body.answers, { threshold: 0.6, longestFirst: false, intent });
  const gold = item.terms ?? item.filterSpans ?? [];
  const goldDiscard = (item.discardSpans ?? []).map((span) => ({ ...span, category: "discard" }));
  records.push({
    id: item.id, split: item.split, group: item.group, q: item.q, locale: item.locale,
    latencyMs: Math.round(performance.now() - started), intent,
    inputTokens: body.usage?.input_tokens ?? null,
    selected, goldFilters: canonical(gold), goldDiscard,
    filterExact: JSON.stringify(canonical(selected)) === JSON.stringify(canonical(gold)),
    discardExact: item.discardSpans ? JSON.stringify(coverage(item.q, selected)) ===
      JSON.stringify(coverage(item.q, goldDiscard)) : null,
  });
  await new Promise((resolve) => setTimeout(resolve, 80));
}
console.log(JSON.stringify({
  timestamp: new Date().toISOString(), variant, dataset: datasetPath, count: records.length,
  filterExact: records.filter((item) => item.filterExact).length,
  discardExact: records.filter((item) => item.discardExact).length,
  discardLabelled: records.filter((item) => item.discardExact !== null).length,
  records,
}, null, 2));
