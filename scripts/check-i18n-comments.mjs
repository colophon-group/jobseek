import { existsSync, readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, extname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const scriptDir = dirname(fileURLToPath(import.meta.url));
const defaultRepoRoot = resolve(scriptDir, "..");
const dependencyRoot = resolve(process.argv[2] ?? defaultRepoRoot);
const ts = require(
  require.resolve("typescript", {
    paths: [join(dependencyRoot, "apps/web"), dependencyRoot],
  }),
);

const SOURCE_DIRS = ["apps/web/app", "apps/web/src"];
const SOURCE_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx"]);

function normalizePath(path) {
  return path.split(sep).join("/");
}

function isTestPath(path) {
  const normalized = normalizePath(path);
  return (
    normalized.includes("/__tests__/") ||
    /\.(test|spec)\.[cm]?[jt]sx?$/.test(normalized)
  );
}

function listSourceFiles(repoRoot) {
  const files = [];

  function walk(dir) {
    if (!existsSync(dir)) {
      return;
    }

    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "node_modules" && entry.name !== ".next") {
          walk(path);
        }
        continue;
      }

      if (SOURCE_EXTENSIONS.has(extname(entry.name)) && !isTestPath(path)) {
        files.push(path);
      }
    }
  }

  for (const sourceDir of SOURCE_DIRS) {
    walk(join(repoRoot, sourceDir));
  }

  return files.sort();
}

function getPropertyName(property) {
  if (
    ts.isPropertyAssignment(property) ||
    ts.isShorthandPropertyAssignment(property) ||
    ts.isMethodDeclaration(property)
  ) {
    const name = property.name;
    if (ts.isIdentifier(name) || ts.isStringLiteral(name) || ts.isNumericLiteral(name)) {
      return name.text;
    }
  }

  return null;
}

function hasProperty(objectLiteral, propertyName) {
  return objectLiteral.properties.some((property) => getPropertyName(property) === propertyName);
}

function getStringProperty(objectLiteral, propertyName) {
  for (const property of objectLiteral.properties) {
    if (!ts.isPropertyAssignment(property) || getPropertyName(property) !== propertyName) {
      continue;
    }

    const initializer = property.initializer;
    if (ts.isStringLiteral(initializer) || ts.isNoSubstitutionTemplateLiteral(initializer)) {
      return initializer.text;
    }
  }

  return null;
}

function isI18nUnderscoreCall(node) {
  if (!ts.isCallExpression(node) || !ts.isPropertyAccessExpression(node.expression)) {
    return false;
  }

  return (
    node.expression.name.text === "_" &&
    ts.isIdentifier(node.expression.expression) &&
    node.expression.expression.text === "i18n"
  );
}

function createSourceFile(filePath, sourceText) {
  const scriptKind = filePath.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  return ts.createSourceFile(filePath, sourceText, ts.ScriptTarget.Latest, true, scriptKind);
}

export function findI18nCommentViolationsInSource(sourceText, filePath) {
  const sourceFile = createSourceFile(filePath, sourceText);
  const violations = [];

  function visit(node) {
    if (isI18nUnderscoreCall(node)) {
      const [descriptor] = node.arguments;
      if (descriptor && ts.isObjectLiteralExpression(descriptor) && hasProperty(descriptor, "id")) {
        if (!hasProperty(descriptor, "comment")) {
          const location = sourceFile.getLineAndCharacterOfPosition(descriptor.getStart(sourceFile));
          violations.push({
            file: filePath,
            line: location.line + 1,
            column: location.character + 1,
            id: getStringProperty(descriptor, "id"),
            message: getStringProperty(descriptor, "message"),
          });
        }
      }
    }

    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
  return violations;
}

export function findI18nCommentViolations(repoRoot = process.cwd()) {
  const absoluteRoot = resolve(repoRoot);
  return listSourceFiles(absoluteRoot).flatMap((filePath) => {
    const sourceText = readFileSync(filePath, "utf8");
    const relativePath = normalizePath(relative(absoluteRoot, filePath));
    return findI18nCommentViolationsInSource(sourceText, relativePath);
  });
}

function formatViolation(violation) {
  const suffix = violation.id ? ` (${violation.id})` : "";
  return `${violation.file}:${violation.line}:${violation.column}${suffix}`;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const repoRoot = process.argv[2] ?? defaultRepoRoot;
  const violations = findI18nCommentViolations(repoRoot);

  if (violations.length > 0) {
    console.error("");
    console.error(`❌ i18n._ descriptors missing translator comments (${violations.length}):`);
    for (const violation of violations) {
      console.error(`   ${formatViolation(violation)}`);
    }
    console.error("");
    console.error("Add a `comment` field that explains where the string appears or how to translate it.");
    process.exit(1);
  }

  console.log("i18n-comments: all i18n._ descriptors include translator comments ✓");
}
