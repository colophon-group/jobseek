import { readFileSync } from "node:fs";
import { exactSuggestion, fieldAgreement, sameShape, selectedRouteSpans, spansForQuery, tokenize } from "./jev-routing-core.mjs";

const [evaluationPath, intentPath, suggestionPath] = process.argv.slice(2);
if (!evaluationPath || !intentPath || !suggestionPath) {
  throw new Error("Usage: node score-jev-routing-config.mjs <eval.json> <intent-gold.json> <human-candidates.ndjson>");
}
const evaluation = JSON.parse(readFileSync(evaluationPath, "utf8"));
const current = JSON.parse(readFileSync("docs/experiments/jev-routing-current-system-2026-09-23.json", "utf8"));
const intent = JSON.parse(readFileSync(intentPath, "utf8"));
const humanSuggestions = readFileSync(suggestionPath, "utf8").trim().split("\n").map(JSON.parse);
const currentById = new Map(current.records.map((r) => [r.id, r]));
const intentById = new Map(intent.records.map((r) => [r.id, r]));
const suggestionsById = new Map(humanSuggestions.map((r) => [r.id, r]));

// Manual adjudication where the first Typesense hit is missing or materially
// different from the intended taxonomy concept. Each slug exists in the
// current taxonomy; these overrides describe gold, not Jev's input.
const goldOverrides = {
  "verbose-04:software engineering": "software-engineer",
  "verbose-08:cybersecurity engineering": "security-engineer",
  "mistakes-05:pyhton": "python",
  "mistakes-06:develper": "software-engineer",
  "mistakes-07:kubernets": "kubernetes",
  "mistakes-08:zurcih": "zurich",
  "mistakes-09:berln": "berlin",
  "multiword-02:New York": "new-york-new-york-united-states",
  "multilingual-01:Softwareentwickler": "software-engineer",
  "multilingual-04:Data Engineer": "data-engineer",
};
const employmentType = (text) => {
  const lower = text.toLowerCase().trim();
  if (["contract", "contractor"].includes(lower)) return "contract";
  if (["part-time", "part time", "teilzeit", "temps partiel"].includes(lower)) return "part_time";
  if (["full-time", "full time", "vollzeit"].includes(lower)) return "full_time";
  if (["temporary", "temp"].includes(lower)) return "temporary";
  if (["volunteer", "voluntary"].includes(lower)) return "volunteer";
  return null;
};
const targetField = { location: "locations", occupation: "occupations", seniority: "seniorities", technology: "technologies",
  remote: "workMode", hybrid: "workMode", onsite: "workMode", employmentType: "employmentTypes" };
function emptyShape() {
  return { keywords: [], locations: [], occupations: [], seniorities: [], technologies: [], workMode: [], employmentTypes: [] };
}
function makeShape(q, selections) {
  const { segments } = spansForQuery(q);
  const consumed = segments.map((words) => Array(words.length).fill(false));
  const shape = emptyShape();
  const proposals = [];
  for (const selection of selections) {
    const { category, segment, start, end, text, slug, suggestions = [] } = selection;
    if (!slug || !targetField[category]) {
      proposals.push({ text, category, status: "unresolved", suggestions: suggestions.slice(0, 3).map((x) => ({ slug: x.slug, name: x.name })) });
      continue;
    }
    const field = targetField[category];
    if (!shape[field].includes(slug)) shape[field].push(slug);
    for (let index = start; index < end; index++) consumed[segment][index] = true;
    proposals.push({ text, category, slug, status: "resolved", suggestions: suggestions.slice(0, 3).map((x) => ({ slug: x.slug, name: x.name })) });
  }
  const seen = new Set();
  segments.forEach((words, segment) => words.forEach((word, index) => {
    if (consumed[segment][index]) return;
    const key = word.toLowerCase();
    if (!seen.has(key)) { seen.add(key); shape.keywords.push(word); }
  }));
  return { shape, proposals };
}
function goldFor(record) {
  const candidateTerms = suggestionsById.get(record.id).terms;
  const selections = record.terms.map((term) => {
    let slug;
    let suggestions = [];
    if (["remote", "hybrid", "onsite"].includes(term.category)) slug = term.category;
    else if (term.category === "employmentType") slug = employmentType(term.text);
    else {
      suggestions = candidateTerms.find((candidate) => candidate.text === term.text && candidate.category === term.category)?.suggestions ?? [];
      slug = goldOverrides[`${record.id}:${term.text}`] ?? suggestions[0]?.slug;
    }
    if (!slug) throw new Error(`Unresolved human target ${record.id} ${term.text}`);
    return { ...term, slug, suggestions };
  });
  return makeShape(record.q, selections);
}
function modelFor(record, normalization) {
  const chosen = selectedRouteSpans(record.q, record.answers, { threshold: 0.6, longestFirst: true });
  const spans = spansForQuery(record.q).spans;
  const selections = chosen.map((term) => {
    const span = spans.find((s) => s.segment === term.segment && s.start === term.start && s.end === term.end);
    const suggestions = record.candidates[span.id] ?? [];
    let slug;
    if (["remote", "hybrid", "onsite"].includes(term.category)) slug = term.category;
    else if (term.category === "employmentType") slug = employmentType(term.text);
    else if (["location", "occupation", "seniority", "technology"].includes(term.category)) {
      slug = (exactSuggestion(term.text, term.category, suggestions) ??
        (normalization === "top" ? suggestions[0] : null))?.slug;
    }
    return { ...term, slug, suggestions };
  });
  return makeShape(record.q, selections);
}
const filterFields = ["locations", "occupations", "seniorities", "technologies", "workMode", "employmentTypes"];
function score(rows, selection) {
  let tp = 0; let fp = 0; let fn = 0;
  for (const row of rows) for (const field of filterFields) {
    const wanted = new Set(row.gold.shape[field]);
    const got = new Set(selection(row).shape[field]);
    for (const value of got) (wanted.has(value) ? tp++ : fp++);
    for (const value of wanted) if (!got.has(value)) fn++;
  }
  const precision = tp / (tp + fp);
  const recall = tp / (tp + fn);
  return {
    exact: rows.filter((row) => sameShape(selection(row).shape, row.gold.shape)).length,
    filtersExact: rows.filter((row) => filterFields.every((field) => fieldAgreement(selection(row).shape, row.gold.shape)[field])).length,
    keywordsExact: rows.filter((row) => fieldAgreement(selection(row).shape, row.gold.shape).keywords).length,
    filterPrecision: precision, filterRecall: recall, filterF1: 2 * precision * recall / (precision + recall),
    groups: Object.fromEntries([...new Set(rows.map((r) => r.group))].map((group) => [group, {
      count: rows.filter((r) => r.group === group).length,
      exact: rows.filter((r) => r.group === group && sameShape(selection(r).shape, r.gold.shape)).length,
    }])),
  };
}
const rows = evaluation.records.map((record) => {
  const gold = goldFor(intentById.get(record.id));
  const parser = { shape: currentById.get(record.id).goldShape };
  return { id: record.id, group: record.group, q: record.q, gold, parser,
    strict: modelFor(record, "strict"), top: modelFor(record, "top") };
});
console.log(JSON.stringify({ variant: evaluation.variant, split: evaluation.split, count: rows.length,
  goldOverrides, parser: score(rows, (r) => r.parser), strict: score(rows, (r) => r.strict), top: score(rows, (r) => r.top),
  rows }, null, 2));
