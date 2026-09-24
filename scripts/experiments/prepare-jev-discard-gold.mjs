import { writeFileSync } from "node:fs";
import { records } from "./jev-routing-discard-labels.mjs";

const target = process.argv[2];
if (!target) throw new Error("Usage: node prepare-jev-discard-gold.mjs <output.json>");
writeFileSync(target, JSON.stringify({
  description: "Editorial search-query labels, including contextual discard spans, frozen before catalog6 holdout evaluation",
  count: records.length,
  tune: records.filter((record) => record.split === "tune").length,
  holdout: records.filter((record) => record.split === "holdout").length,
  records,
}, null, 2) + "\n");
