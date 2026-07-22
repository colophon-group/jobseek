#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const LOCALES = ["en", "de", "fr", "it"];

export function extractCatalogIds(poSource) {
  const ids = new Set();
  const msgidPattern = /^msgid "((?:[^"\\]|\\.)+)"$/gm;

  for (const match of poSource.matchAll(msgidPattern)) {
    ids.add(JSON.parse(`"${match[1]}"`));
  }

  return [...ids].sort();
}

export function findMissingCompiledIds(ids, messages) {
  return ids.filter((id) => !Object.hasOwn(messages, id));
}

async function main(repoRoot) {
  const localesDir = path.join(repoRoot, "apps", "web", "locales");
  let failed = false;

  for (const locale of LOCALES) {
    const poPath = path.join(localesDir, `${locale}.po`);
    const compiledPath = path.join(localesDir, `${locale}.js`);
    const poSource = await readFile(poPath, "utf8");
    const compiledUrl = `${pathToFileURL(compiledPath).href}?coverage=${Date.now()}`;
    const { messages } = await import(compiledUrl);
    const missing = findMissingCompiledIds(extractCatalogIds(poSource), messages);

    if (missing.length > 0) {
      failed = true;
      console.error(`\n❌ apps/web/locales/${locale}.js — ${missing.length} catalog IDs missing after compile:`);
      for (const id of missing) console.error(`     ${id}`);
    }
  }

  if (failed) process.exitCode = 1;
  else console.log("i18n-compiled: every committed catalog ID is present after compile ✓");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main(path.resolve(process.argv[2] ?? process.cwd()));
}
