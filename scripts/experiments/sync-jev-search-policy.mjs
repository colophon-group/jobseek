import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { JEV_ROUTING_CATEGORIES_WITH_DISCARD, JEV_ROUTING_POLICY_CATALOG7, JEV_ROUTING_INTENT_CRITERIA } from "./jev-routing-core.mjs";

const root = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const catalog = JSON.parse(readFileSync(resolve(root, "scripts/experiments/jev-occupation-catalog.json"), "utf8"));
const target = resolve(root, "apps/web/src/lib/search/jev-query-policy.json");
const expected = JSON.stringify({
  version: "jev-search-catalog7-v1",
  model: "jev-1.13.0",
  policy: JEV_ROUTING_POLICY_CATALOG7,
  categories: JEV_ROUTING_CATEGORIES_WITH_DISCARD,
  intentCriteria: JEV_ROUTING_INTENT_CRITERIA,
  occupationCatalog: catalog,
}, null, 2) + "\n";

if (process.argv.includes("--check")) {
  if (readFileSync(target, "utf8") !== expected) {
    throw new Error("Production Jev query policy differs from the evaluated source; run sync-jev-search-policy.mjs");
  }
} else {
  writeFileSync(target, expected);
}
