import { readFileSync } from "node:fs";
import { intentLabels } from "./jev-routing-intent-labels.mjs";
import { selectedRouteSpans, spansForQuery } from "./jev-routing-core.mjs";

const [mode, evalPath, thresholdArg, longestArg] = process.argv.slice(2);
if (!["validate", "grid", "fixed"].includes(mode)) throw new Error("Use validate|grid|fixed");
const evaluation = evalPath ? JSON.parse(readFileSync(evalPath, "utf8")) : null;
const goldPath = "docs/experiments/jev-routing-current-system-2026-09-23.json";
const gold = JSON.parse(readFileSync(goldPath, "utf8"));
const labelled = gold.records.map((record) => {
  const terms = (intentLabels[record.id] ?? "").split("|").filter(Boolean).map((label) => {
    const at = label.lastIndexOf(":");
    return { text: label.slice(0, at), category: label.slice(at + 1) };
  }).filter(({ category }) => category !== "keyword");
  const available = spansForQuery(record.q).spans;
  for (const term of terms) {
    const hits = available.filter((span) => span.text.toLowerCase() === term.text.toLowerCase());
    if (hits.length !== 1) throw new Error(`${record.id}: label ${term.text} matched ${hits.length} spans`);
    Object.assign(term, { segment: hits[0].segment, start: hits[0].start, end: hits[0].end });
  }
  for (const [index, term] of terms.entries()) for (const other of terms.slice(index + 1)) {
    if (term.segment === other.segment && term.start < other.end && other.start < term.end) {
      throw new Error(`${record.id}: overlapping human labels`);
    }
  }
  return { id: record.id, group: record.group, split: record.split, q: record.q, locale: record.locale, terms };
});
if (Object.keys(intentLabels).length !== gold.records.length) throw new Error("Label count mismatch");
if (mode === "validate") {
  console.log(JSON.stringify({ count: labelled.length, labelledSpans: labelled.reduce((n, x) => n + x.terms.length, 0), records: labelled }, null, 2));
  process.exit(0);
}
if (!evaluation) throw new Error("Evaluation JSON required");
const byId = new Map(labelled.map((record) => [record.id, record]));
const key = (term) => `${term.segment}:${term.start}:${term.end}:${term.category}`;
function score(config) {
  const rows = evaluation.records.map((record) => {
    const goldRecord = byId.get(record.id);
    const expected = new Set(goldRecord.terms.map(key));
    const selected = selectedRouteSpans(record.q, record.answers, config);
    const predicted = new Set(selected.map(key));
    const tp = [...predicted].filter((item) => expected.has(item)).length;
    return { id: record.id, group: record.group, q: record.q, expected: goldRecord.terms, selected, tp,
      fp: predicted.size - tp, fn: expected.size - tp,
      exact: tp === expected.size && tp === predicted.size };
  });
  const tp = rows.reduce((n, x) => n + x.tp, 0);
  const fp = rows.reduce((n, x) => n + x.fp, 0);
  const fn = rows.reduce((n, x) => n + x.fn, 0);
  const precision = tp / (tp + fp);
  const recall = tp / (tp + fn);
  return {
    config, count: rows.length, exact: rows.filter((x) => x.exact).length,
    spanPrecision: precision, spanRecall: recall, spanF1: 2 * precision * recall / (precision + recall),
    groups: Object.fromEntries([...new Set(rows.map((x) => x.group))].map((group) => [group, {
      count: rows.filter((x) => x.group === group).length,
      exact: rows.filter((x) => x.group === group && x.exact).length,
    }])),
    mismatches: rows.filter((x) => !x.exact),
  };
}
if (mode === "fixed") {
  console.log(JSON.stringify({ variant: evaluation.variant, split: evaluation.split,
    ...score({ threshold: Number(thresholdArg), longestFirst: longestArg === "true" }) }, null, 2));
} else {
  const results = [0.3, 0.45, 0.6, 0.75, 0.9].flatMap((threshold) =>
    [false, true].map((longestFirst) => score({ threshold, longestFirst })));
  results.sort((a, b) => b.exact - a.exact || b.spanF1 - a.spanF1);
  console.log(JSON.stringify({ variant: evaluation.variant, split: evaluation.split,
    top: results.map(({ mismatches, ...summary }) => summary), bestMismatches: results[0].mismatches }, null, 2));
}
