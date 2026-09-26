import { readFileSync } from "node:fs";
import { reconstruct, sameShape, fieldAgreement } from "./jev-routing-core.mjs";

const [mode, path, thresholdArg, literalArg, longestArg] = process.argv.slice(2);
if (!["grid", "fixed"].includes(mode) || !path) {
  throw new Error("Usage: node score-jev-routing.mjs <grid|fixed> <evaluation.json> [threshold] [literalWorkMode] [longestFirst]");
}
const evaluation = JSON.parse(readFileSync(path, "utf8"));
const fields = ["keywords", "locations", "occupations", "seniorities", "technologies", "workMode", "employmentTypes"];
const filterFields = fields.filter((field) => field !== "keywords");

function evaluate(config) {
  const rows = evaluation.records.map((record) => {
    const prediction = reconstruct(record.q, record.answers, record.candidates, config);
    const agreement = fieldAgreement(prediction.shape, record.goldShape);
    return { record, prediction, agreement, exact: sameShape(prediction.shape, record.goldShape) };
  });
  const fieldExact = Object.fromEntries(fields.map((field) => [field, rows.filter((row) => row.agreement[field]).length]));
  const groups = Object.fromEntries([...new Set(rows.map((row) => row.record.group))].map((group) => [
    group,
    { count: rows.filter((row) => row.record.group === group).length, exact: rows.filter((row) => row.record.group === group && row.exact).length },
  ]));
  let tp = 0; let fp = 0; let fn = 0;
  for (const row of rows) for (const field of filterFields) {
    const wanted = new Set(row.record.goldShape[field]);
    const got = new Set(row.prediction.shape[field]);
    for (const value of got) (wanted.has(value) ? tp++ : fp++);
    for (const value of wanted) if (!got.has(value)) fn++;
  }
  const precision = tp + fp ? tp / (tp + fp) : 1;
  const recall = tp + fn ? tp / (tp + fn) : 1;
  return {
    config,
    count: rows.length,
    exact: rows.filter((row) => row.exact).length,
    filtersExact: rows.filter((row) => filterFields.every((field) => row.agreement[field])).length,
    keywordsExact: fieldExact.keywords,
    fieldExact,
    groups,
    filterPrecision: precision,
    filterRecall: recall,
    filterF1: precision + recall ? 2 * precision * recall / (precision + recall) : 0,
    mismatches: rows.filter((row) => !row.exact).map((row) => ({
      id: row.record.id,
      q: row.record.q,
      gold: row.record.goldShape,
      prediction: row.prediction.shape,
      selectedSpans: row.prediction.selectedSpans,
    })),
  };
}

if (mode === "grid") {
  const configs = [0.3, 0.45, 0.6, 0.75, 0.9].flatMap((threshold) =>
    [false, true].flatMap((literalWorkMode) =>
      [false, true].map((longestFirst) => ({ threshold, literalWorkMode, longestFirst }))));
  const scored = configs.map(evaluate).sort((a, b) =>
    b.exact - a.exact || b.filtersExact - a.filtersExact || b.filterF1 - a.filterF1 ||
    a.config.threshold - b.config.threshold);
  console.log(JSON.stringify({ variant: evaluation.variant, split: evaluation.split, top: scored.slice(0, 10).map(({ mismatches, ...rest }) => rest), bestMismatches: scored[0].mismatches }, null, 2));
} else {
  const config = {
    threshold: Number(thresholdArg),
    literalWorkMode: literalArg === "true",
    longestFirst: longestArg === "true",
  };
  console.log(JSON.stringify({ variant: evaluation.variant, split: evaluation.split, ...evaluate(config) }, null, 2));
}
