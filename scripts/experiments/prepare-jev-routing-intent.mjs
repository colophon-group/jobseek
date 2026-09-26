import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { spansForQuery } from "./jev-routing-core.mjs";

const [currentPath, labelsPath] = process.argv.slice(2);
if (!currentPath || !labelsPath) throw new Error("Usage: node prepare-jev-routing-intent.mjs <current-system.json> <labels.mjs>");
const current = JSON.parse(readFileSync(currentPath, "utf8"));
const { intentLabels } = await import(pathToFileURL(resolve(labelsPath)).href);
if (current.records.length !== Object.keys(intentLabels).length) throw new Error("Label count mismatch");
const records = current.records.map((record) => {
  if (!(record.id in intentLabels)) throw new Error(`Missing label ${record.id}`);
  const spans = spansForQuery(record.q).spans;
  const terms = intentLabels[record.id].split("|").filter(Boolean).map((label) => {
    const index = label.lastIndexOf(":");
    const text = label.slice(0, index);
    const category = label.slice(index + 1);
    const matches = spans.filter((span) => span.text.toLowerCase() === text.toLowerCase());
    if (matches.length !== 1) throw new Error(`${record.id}: ${text} matched ${matches.length} spans`);
    return { text, category, segment: matches[0].segment, start: matches[0].start, end: matches[0].end };
  }).filter((term) => term.category !== "keyword");
  for (let i = 0; i < terms.length; i++) for (const other of terms.slice(i + 1)) {
    const term = terms[i];
    if (term.segment === other.segment && term.start < other.end && other.start < term.end) {
      throw new Error(`${record.id}: overlapping human labels`);
    }
  }
  return { id: record.id, group: record.group, split: record.split, q: record.q, locale: record.locale, terms };
});
console.log(JSON.stringify({ count: records.length, labelledSpans: records.reduce((sum, record) => sum + record.terms.length, 0), records }, null, 2));
