/**
 * Web runtime boundary for issue #6248.
 *
 * Crawler postings are owned by the crawler's local PostgreSQL pipeline. The
 * web app may consume indexed posting data, but it must not become a second
 * writer to the Supabase crawler mirror.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, relative, resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const webRoot = resolve(__dirname, "../../..");
const runtimeRoots = ["app", "src"];
const runtimeExtensions = new Set([
  ".ts",
  ".tsx",
  ".mts",
  ".cts",
  ".js",
  ".jsx",
  ".mjs",
  ".cjs",
]);
const developmentOnlyFiles = new Set([join(webRoot, "src/db/seed.ts")]);
const fixtureRoot = join(
  webRoot,
  "src/db/__tests__/fixtures/crawler-data-write-boundary",
);

function listSourceFiles(dir: string, skipTests: boolean): string[] {
  const files: string[] = [];

  for (const entry of readdirSync(dir)) {
    if (
      entry === ".next" ||
      entry === "node_modules" ||
      (skipTests && entry === "__tests__")
    ) {
      continue;
    }

    const path = join(dir, entry);
    const stat = statSync(path);

    if (stat.isDirectory()) {
      files.push(...listSourceFiles(path, skipTests));
      continue;
    }

    if (
      stat.isFile() &&
      runtimeExtensions.has(extname(path)) &&
      !developmentOnlyFiles.has(path) &&
      !(skipTests && path.match(/\.(?:test|spec)\.[^.]+$/))
    ) {
      files.push(path);
    }
  }

  return files;
}

const runtimeFiles = runtimeRoots.flatMap((root) =>
  listSourceFiles(join(webRoot, root), true),
);
const fixtureFiles = listSourceFiles(fixtureRoot, false);

function createAnalysisProgram(): ts.Program {
  const configPath = join(webRoot, "tsconfig.json");
  const config = ts.readConfigFile(configPath, ts.sys.readFile);
  if (config.error) {
    throw new Error(ts.flattenDiagnosticMessageText(config.error.messageText, "\n"));
  }

  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, webRoot);
  return ts.createProgram({
    rootNames: [...new Set([...runtimeFiles, ...fixtureFiles])],
    options: {
      ...parsed.options,
      allowJs: true,
      checkJs: false,
      incremental: false,
      noEmit: true,
    },
  });
}

const program = createAnalysisProgram();
const checker = program.getTypeChecker();
const schemaPath = join(webRoot, "src/db/schema.ts");
const schemaSource = program.getSourceFile(schemaPath);

if (!schemaSource) {
  throw new Error(`TypeScript program did not load ${schemaPath}`);
}

const schemaModule = checker.getSymbolAtLocation(schemaSource);
if (!schemaModule) {
  throw new Error("TypeScript could not resolve the database schema module");
}

function resolveAlias(symbol: ts.Symbol): ts.Symbol {
  const seen = new Set<ts.Symbol>();
  let current = symbol;

  while (current.flags & ts.SymbolFlags.Alias) {
    if (seen.has(current)) break;
    seen.add(current);
    const next = checker.getAliasedSymbol(current);
    if (next === current) break;
    current = next;
  }

  return current;
}

const exportedJobPosting = checker
  .getExportsOfModule(schemaModule)
  .find((symbol) => symbol.name === "jobPosting");

if (!exportedJobPosting) {
  throw new Error("TypeScript could not resolve the jobPosting schema export");
}

const jobPostingSymbol = resolveAlias(exportedJobPosting);
const jobPostingDeclaration =
  jobPostingSymbol.valueDeclaration ?? jobPostingSymbol.declarations?.[0];

if (!jobPostingDeclaration) {
  throw new Error("jobPosting does not have a value declaration");
}

const jobPostingType = checker.getTypeOfSymbolAtLocation(
  jobPostingSymbol,
  jobPostingDeclaration,
);

function unwrapExpression(expression: ts.Expression): ts.Expression {
  let current = expression;

  while (
    ts.isParenthesizedExpression(current) ||
    ts.isAsExpression(current) ||
    ts.isTypeAssertionExpression(current) ||
    ts.isNonNullExpression(current) ||
    ts.isSatisfiesExpression(current)
  ) {
    current = current.expression;
  }

  return current;
}

function isUsableType(type: ts.Type): boolean {
  return !(type.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown | ts.TypeFlags.Never));
}

function hasJobPostingType(type: ts.Type): boolean {
  if (type.isUnionOrIntersection()) {
    return type.types.some(hasJobPostingType);
  }
  if (!isUsableType(type)) return false;

  return (
    type === jobPostingType ||
    (checker.isTypeAssignableTo(type, jobPostingType) &&
      checker.isTypeAssignableTo(jobPostingType, type))
  );
}

function symbolReferencesJobPosting(
  symbol: ts.Symbol | undefined,
  seen: Set<ts.Symbol>,
): boolean {
  if (!symbol) return false;

  const resolved = resolveAlias(symbol);
  if (resolved === jobPostingSymbol) return true;
  if (seen.has(resolved)) return false;
  seen.add(resolved);

  return (resolved.declarations ?? []).some((declaration) => {
    if (
      (ts.isVariableDeclaration(declaration) ||
        ts.isPropertyDeclaration(declaration) ||
        ts.isPropertyAssignment(declaration) ||
        ts.isParameter(declaration)) &&
      declaration.initializer
    ) {
      return expressionReferencesJobPosting(declaration.initializer, seen);
    }

    if (ts.isShorthandPropertyAssignment(declaration)) {
      return symbolReferencesJobPosting(
        checker.getShorthandAssignmentValueSymbol(declaration),
        seen,
      );
    }

    return false;
  });
}

function expressionReferencesJobPosting(
  input: ts.Expression,
  seen = new Set<ts.Symbol>(),
): boolean {
  const expression = unwrapExpression(input);
  const type = checker.getTypeAtLocation(expression);
  if (hasJobPostingType(type)) return true;

  const symbolTarget = ts.isPropertyAccessExpression(expression)
    ? expression.name
    : ts.isElementAccessExpression(expression)
      ? expression.argumentExpression
      : expression;
  if (
    symbolReferencesJobPosting(
      checker.getSymbolAtLocation(symbolTarget),
      new Set(seen),
    )
  ) {
    return true;
  }

  if (ts.isConditionalExpression(expression)) {
    return (
      expressionReferencesJobPosting(expression.whenTrue, new Set(seen)) ||
      expressionReferencesJobPosting(expression.whenFalse, new Set(seen))
    );
  }

  if (ts.isPropertyAccessExpression(expression)) {
    return symbolReferencesJobPosting(
      checker.getSymbolAtLocation(expression.name),
      new Set(seen),
    );
  }

  return false;
}

function calledMethodName(expression: ts.LeftHandSideExpression): string | null {
  if (ts.isPropertyAccessExpression(expression)) return expression.name.text;
  if (
    ts.isElementAccessExpression(expression) &&
    ts.isStringLiteralLike(expression.argumentExpression)
  ) {
    return expression.argumentExpression.text;
  }
  return null;
}

function staticString(
  input: ts.Expression,
  seen = new Set<ts.Symbol>(),
): string | null {
  const expression = unwrapExpression(input);
  if (ts.isStringLiteralLike(expression)) return expression.text;

  if (
    ts.isBinaryExpression(expression) &&
    expression.operatorToken.kind === ts.SyntaxKind.PlusToken
  ) {
    const left = staticString(expression.left, new Set(seen));
    const right = staticString(expression.right, new Set(seen));
    return left == null || right == null ? null : left + right;
  }

  const symbol = checker.getSymbolAtLocation(expression);
  if (!symbol) return null;
  const resolved = resolveAlias(symbol);
  if (seen.has(resolved)) return null;
  seen.add(resolved);

  for (const declaration of resolved.declarations ?? []) {
    if (ts.isVariableDeclaration(declaration) && declaration.initializer) {
      const value = staticString(declaration.initializer, seen);
      if (value != null) return value;
    }
  }

  return null;
}

function originatesFromSupabaseJobPosting(
  input: ts.Expression,
  seen = new Set<ts.Symbol>(),
): boolean {
  const expression = unwrapExpression(input);

  if (ts.isCallExpression(expression)) {
    const method = calledMethodName(expression.expression);
    if (
      method === "from" &&
      expression.arguments[0] &&
      staticString(expression.arguments[0]) === "job_posting"
    ) {
      return true;
    }

    if (
      (ts.isPropertyAccessExpression(expression.expression) ||
        ts.isElementAccessExpression(expression.expression)) &&
      originatesFromSupabaseJobPosting(expression.expression.expression, seen)
    ) {
      return true;
    }
  }

  const symbol = checker.getSymbolAtLocation(expression);
  if (!symbol) return false;
  const resolved = resolveAlias(symbol);
  if (seen.has(resolved)) return false;
  seen.add(resolved);

  return (resolved.declarations ?? []).some(
    (declaration) =>
      ts.isVariableDeclaration(declaration) &&
      Boolean(
        declaration.initializer &&
          originatesFromSupabaseJobPosting(declaration.initializer, seen),
      ),
  );
}

const rawSqlMutation =
  /\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?:["`]?public["`]?\s*\.\s*)?["`]?job_posting\b["`]?/i;

function mutatesCrawlerPostings(sourceFile: ts.SourceFile): boolean {
  if (rawSqlMutation.test(sourceFile.text)) return true;

  let mutationFound = false;
  const visit = (node: ts.Node): void => {
    if (mutationFound) return;

    if (ts.isCallExpression(node)) {
      const method = calledMethodName(node.expression);
      if (
        method &&
        ["insert", "update", "delete"].includes(method) &&
        node.arguments[0] &&
        expressionReferencesJobPosting(node.arguments[0])
      ) {
        mutationFound = true;
        return;
      }

      if (
        method &&
        ["insert", "update", "upsert", "delete"].includes(method) &&
        (ts.isPropertyAccessExpression(node.expression) ||
          ts.isElementAccessExpression(node.expression)) &&
        originatesFromSupabaseJobPosting(node.expression.expression)
      ) {
        mutationFound = true;
        return;
      }
    }

    ts.forEachChild(node, visit);
  };

  visit(sourceFile);
  return mutationFound;
}

function sourceFile(path: string): ts.SourceFile {
  const source = program.getSourceFile(path);
  if (!source) throw new Error(`TypeScript program did not load ${path}`);
  return source;
}

describe("web crawler-data write boundary (#6248)", () => {
  it("keeps the retired Meta Apify endpoint and importer absent", () => {
    for (const extension of runtimeExtensions) {
      expect(
        existsSync(
          join(webRoot, `app/api/admin/meta/apify-import/route${extension}`),
        ),
      ).toBe(false);
      expect(
        existsSync(join(webRoot, `src/lib/admin/meta-apify-import${extension}`)),
      ).toBe(false);
    }
  });

  it("does not mutate crawler job_posting data from web runtime code", () => {
    const offenders = runtimeFiles
      .filter((path) => mutatesCrawlerPostings(sourceFile(path)))
      .map((path) => relative(webRoot, path))
      .sort();

    expect(offenders).toEqual([]);
  });

  it.each([
    "relative-named.ts",
    "namespace.ts",
    "named-alias.ts",
    "re-export-consumer.ts",
    "structural-indirection.ts",
    "javascript.js",
    "module.mjs",
    "commonjs.cjs",
    "raw-sql.ts",
    "supabase.ts",
  ])("detects the malicious %s fixture", (name) => {
    expect(mutatesCrawlerPostings(sourceFile(join(fixtureRoot, name)))).toBe(true);
  });

  it("does not restore Apify access in the web runtime", () => {
    const apifyRuntimeMarker = /\bAPIFY_TOKEN\b|api\.apify\.com\/v2/;
    const offenders = runtimeFiles
      .filter((path) => apifyRuntimeMarker.test(readFileSync(path, "utf8")))
      .map((path) => relative(webRoot, path))
      .sort();

    expect(offenders).toEqual([]);
  });
});
