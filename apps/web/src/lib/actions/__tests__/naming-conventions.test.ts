import { readdirSync, readFileSync, statSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { describe, expect, it } from "vitest";

interface ExportedAction {
  file: string;
  name: string;
}

const ACTIONS_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const FETCH_ACTION_FILES = new Set([
  "bootstrap.ts",
  "company-page-data.ts",
  "explore-page-data.ts",
  "watchlist-page-data.ts",
]);
const FETCH_ACTION_NAME =
  /^fetch(?:AppBootstrap|[A-Z][A-Za-z0-9]*(?:PageData|PageDefaults))$/;

function hasModifier(node: ts.Node, kind: ts.SyntaxKind): boolean {
  return ts.canHaveModifiers(node)
    ? (ts.getModifiers(node)?.some((modifier) => modifier.kind === kind) ?? false)
    : false;
}

function actionFiles(): string[] {
  return readdirSync(ACTIONS_DIR)
    .map((entry) => join(ACTIONS_DIR, entry))
    .filter((path) => statSync(path).isFile() && path.endsWith(".ts"))
    .sort();
}

function exportedAsyncFunctions(file: string): ExportedAction[] {
  const source = ts.createSourceFile(
    file,
    readFileSync(file, "utf8"),
    ts.ScriptTarget.Latest,
    true,
  );

  return source.statements.flatMap((statement): ExportedAction[] => {
    if (!ts.isFunctionDeclaration(statement) || statement.name == null) {
      return [];
    }
    if (!hasModifier(statement, ts.SyntaxKind.ExportKeyword)) {
      return [];
    }
    if (!hasModifier(statement, ts.SyntaxKind.AsyncKeyword)) {
      return [];
    }
    return [{ file: basename(file), name: statement.name.text }];
  });
}

function exportedActions(): ExportedAction[] {
  return actionFiles().flatMap(exportedAsyncFunctions);
}

describe("server action naming conventions", () => {
  it("keeps exported action function names globally unique", () => {
    const byName = new Map<string, string[]>();
    for (const action of exportedActions()) {
      byName.set(action.name, [...(byName.get(action.name) ?? []), action.file]);
    }

    const duplicates = Array.from(byName.entries())
      .filter(([, files]) => files.length > 1)
      .map(([name, files]) => `${name}: ${files.join(", ")}`)
      .sort();

    expect(duplicates).toEqual([]);
  });

  it("reserves fetch* for bootstrap and page-data bundlers", () => {
    const violations: string[] = [];

    for (const action of exportedActions()) {
      const fileAllowsFetch = FETCH_ACTION_FILES.has(action.file);
      const isFetch = action.name.startsWith("fetch");

      if (action.name.startsWith("load")) {
        violations.push(`${action.file}:${action.name} should use get* or a domain verb`);
      }
      if (isFetch && !fileAllowsFetch) {
        violations.push(`${action.file}:${action.name} uses fetch* outside a bundle file`);
      }
      if (fileAllowsFetch && !isFetch) {
        violations.push(`${action.file}:${action.name} should use fetch*`);
      }
      if (isFetch && !FETCH_ACTION_NAME.test(action.name)) {
        violations.push(
          `${action.file}:${action.name} should be fetchAppBootstrap, fetch*PageData, or fetch*PageDefaults`,
        );
      }
    }

    expect(violations).toEqual([]);
  });
});
