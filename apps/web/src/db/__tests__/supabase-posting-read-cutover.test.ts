import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const root = process.cwd();
const source = (path: string) => readFileSync(join(root, path), "utf8");

const RAW_JOB_POSTING_SQL =
  /\b(?:from|join)\s+(?:(?:"public"|public)\s*\.\s*)?(?:"job_posting"|job_posting)(?![a-z0-9_])/i;

function isSchemaModule(specifier: string): boolean {
  const normalized = specifier
    .replaceAll("\\", "/")
    .replace(/\.[cm]?[jt]sx?$/, "");
  return normalized === "@/db/schema" || /(?:^|\/)db\/schema$/.test(normalized);
}

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

function findWatchlistPostingReadViolations(
  contents: string,
  fileName = "fixture.ts",
): string[] {
  const file = ts.createSourceFile(
    fileName,
    contents,
    ts.ScriptTarget.Latest,
    true,
    fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const postingBindings = new Set<string>();
  const schemaNamespaces = new Set<string>();
  const postingImportNodes: ts.Node[] = [];

  for (const statement of file.statements) {
    if (
      ts.isImportDeclaration(statement) &&
      ts.isStringLiteral(statement.moduleSpecifier) &&
      isSchemaModule(statement.moduleSpecifier.text)
    ) {
      const bindings = statement.importClause?.namedBindings;
      if (bindings && ts.isNamedImports(bindings)) {
        for (const element of bindings.elements) {
          const importedName = element.propertyName?.text ?? element.name.text;
          if (importedName === "jobPosting") {
            postingBindings.add(element.name.text);
            postingImportNodes.push(element);
          }
        }
      } else if (bindings && ts.isNamespaceImport(bindings)) {
        schemaNamespaces.add(bindings.name.text);
      }
    }

    if (
      ts.isImportEqualsDeclaration(statement) &&
      ts.isExternalModuleReference(statement.moduleReference) &&
      statement.moduleReference.expression &&
      ts.isStringLiteral(statement.moduleReference.expression) &&
      isSchemaModule(statement.moduleReference.expression.text)
    ) {
      schemaNamespaces.add(statement.name.text);
    }
  }

  const usesPostingTable = (input: ts.Expression): boolean => {
    const expression = unwrapExpression(input);
    if (ts.isIdentifier(expression)) return postingBindings.has(expression.text);
    if (ts.isPropertyAccessExpression(expression)) {
      return (
        ts.isIdentifier(expression.expression) &&
        schemaNamespaces.has(expression.expression.text) &&
        expression.name.text === "jobPosting"
      );
    }
    if (ts.isElementAccessExpression(expression)) {
      const argument = expression.argumentExpression;
      return (
        ts.isIdentifier(expression.expression) &&
        schemaNamespaces.has(expression.expression.text) &&
        argument !== undefined &&
        ts.isStringLiteralLike(argument) &&
        argument.text === "jobPosting"
      );
    }
    return false;
  };

  const methodName = (expression: ts.Expression): string | null => {
    if (ts.isPropertyAccessExpression(expression)) return expression.name.text;
    if (
      ts.isElementAccessExpression(expression) &&
      expression.argumentExpression &&
      ts.isStringLiteralLike(expression.argumentExpression)
    ) {
      return expression.argumentExpression.text;
    }
    return null;
  };

  const violations: string[] = [];
  const report = (node: ts.Node, message: string) => {
    const { line } = file.getLineAndCharacterOfPosition(node.getStart(file));
    violations.push(`${fileName}:${line + 1}: ${message}`);
  };

  for (const node of postingImportNodes) {
    report(node, "crawler-owned jobPosting schema import");
  }

  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node) && node.arguments[0]) {
      const method = methodName(node.expression);
      if (
        method &&
        (method === "from" || method === "join" || method.endsWith("Join")) &&
        usesPostingTable(node.arguments[0])
      ) {
        report(node, `Drizzle ${method}(jobPosting) read`);
      }
    }

    if (
      (ts.isStringLiteralLike(node) || ts.isTaggedTemplateExpression(node)) &&
      RAW_JOB_POSTING_SQL.test(node.getText(file))
    ) {
      report(node, "raw SQL job_posting read");
    }

    if (
      (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) &&
      usesPostingTable(node)
    ) {
      report(node, "crawler-owned schema namespace jobPosting access");
    }

    ts.forEachChild(node, visit);
  };
  visit(file);
  return [...new Set(violations)];
}

function walkTypescriptFiles(directory: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...walkTypescriptFiles(path));
    } else if (/\.[cm]?[jt]sx?$/.test(entry.name)) {
      files.push(path);
    }
  }
  return files;
}

function deployedWatchlistRuntimePaths(): string[] {
  const discovered = [join(root, "src"), join(root, "app")]
    .flatMap(walkTypescriptFiles)
    .map((path) => relative(root, path).replaceAll("\\", "/"))
    .filter((path) => path.toLowerCase().includes("watchlist"))
    .filter((path) => !path.includes("/__tests__/") && !/\.(?:test|spec)\.[cm]?[jt]sx?$/.test(path));

  return [...new Set([...discovered, "src/lib/search/search-runner.ts"])].sort();
}

describe("Supabase posting read cutover", () => {
  it.each([
    "src/lib/actions/saved-jobs.ts",
    "src/lib/actions/my-jobs.ts",
  ])("%s reads only saved-job-owned display fields", (path) => {
    const contents = source(path);
    const readSection = path.endsWith("my-jobs.ts")
      ? contents.slice(
          contents.indexOf("export async function getMyJobs"),
          contents.indexOf("export async function updateJobStatus"),
        )
      : contents.slice(contents.indexOf("export async function getSavedJobs"));
    const schemaImport =
      contents.match(/import\s+\{([^}]*)\}\s+from\s+"@\/db\/schema"/)?.[1] ?? "";

    expect(schemaImport).not.toMatch(/\b(jobPosting|company)\b/);
    expect(readSection).not.toContain(".innerJoin(");
  });

  it("posting detail delegates to Typesense without crawler-table SQL", () => {
    const contents = source("src/lib/services/search.ts");
    const detailSection = contents.slice(
      contents.indexOf("export async function getPostingDetail"),
      contents.indexOf("export async function searchJobs"),
    );

    expect(detailSection).toContain("fetchIndexedPostingDetail");
    expect(detailSection).not.toContain("db.execute");
    expect(detailSection).not.toMatch(/FROM\s+(job_posting|company|location)/i);
  });

  it("keeps every deployed watchlist runtime off the crawler posting table", () => {
    const paths = deployedWatchlistRuntimePaths();
    expect(paths).toEqual(
      expect.arrayContaining([
        "src/lib/actions/watchlists.ts",
        "src/lib/actions/watchlist-page-data.ts",
        "src/lib/search/search-runner.ts",
        "src/lib/search/typesense-browser-watchlist.ts",
        "src/lib/services/watchlists.ts",
      ]),
    );

    const violations = paths.flatMap((path) =>
      findWatchlistPostingReadViolations(source(path), path),
    );
    expect(violations).toEqual([]);
  });

  it.each([
    [
      "named schema import",
      'import { jobPosting } from "@/db/schema";\ndb.select().from(jobPosting);',
    ],
    [
      "aliased relative schema import",
      'import { jobPosting as posting } from "../../db/schema";\ndb.select().from(posting);',
    ],
    [
      "namespace schema import",
      'import * as schema from "@/db/schema";\ndb.select().leftJoin(schema.jobPosting, predicate);',
    ],
    [
      "computed namespace access",
      'import * as tables from "../db/schema";\ndb.select().join(tables["jobPosting"], predicate);',
    ],
    [
      "import-equals namespace",
      'import tables = require("../../db/schema");\ndb.select()["from"](tables.jobPosting);',
    ],
    [
      "raw tagged SQL",
      'const query = sql`SELECT * FROM public."job_posting" WHERE is_active`;',
    ],
    [
      "raw string SQL",
      'db.execute("SELECT w.id FROM watchlist w JOIN job_posting jp ON true");',
    ],
  ])("detects %s bypasses", (_label, fixture) => {
    expect(findWatchlistPostingReadViolations(fixture)).not.toEqual([]);
  });

  it("allows Typesense collection names and unrelated web-owned tables", () => {
    const fixture = [
      'import { watchlist } from "@/db/schema";',
      'db.select().from(watchlist);',
      'client.collections("job_posting").documents().search({ q: "*" });',
    ].join("\n");

    expect(findWatchlistPostingReadViolations(fixture)).toEqual([]);
  });
});
