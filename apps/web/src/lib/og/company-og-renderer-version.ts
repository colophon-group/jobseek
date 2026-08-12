import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const COMPANY_OG_HASH_VERSION = "company-og-v3";

const COMPANY_OG_HASH_INPUTS = [
  "src/lib/og/company-og-card.tsx",
  "src/lib/og/render-company-og.tsx",
  "public/fonts/JetBrainsMono-Bold.ttf",
];

const HASHED_EXTENSIONS = new Set([
  ".css",
  ".json",
  ".mjs",
  ".ts",
  ".tsx",
  ".ttf",
  ".yaml",
]);

function collectHashInputFiles(rootDir: string, inputPath: string): string[] {
  const absolute = path.join(rootDir, inputPath);
  if (!fs.existsSync(absolute)) return [absolute];
  const stat = fs.statSync(absolute);
  if (stat.isFile()) return [absolute];
  if (!stat.isDirectory()) return [];

  const files: string[] = [];
  const visit = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (
        entry.name === "node_modules" ||
        entry.name === ".next" ||
        entry.name === "coverage"
      ) {
        continue;
      }
      const child = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        visit(child);
      } else if (entry.isFile() && HASHED_EXTENSIONS.has(path.extname(entry.name))) {
        files.push(child);
      }
    }
  };
  visit(absolute);
  return files;
}

/**
 * Compute the R2 namespace shared by the Next.js route and the off-platform
 * prewarmer. Keeping this in a pure Node module prevents the two execution
 * environments from silently disagreeing about cache keys.
 */
export function computeCompanyOgRendererVersion(
  rootDir: string,
  salt = process.env.COMPANY_OG_RENDERER_VERSION_SALT ?? "",
): string {
  const hash = crypto.createHash("sha256");
  hash.update(`${COMPANY_OG_HASH_VERSION}\n`);
  hash.update(`salt:${salt}\n`);

  const files = COMPANY_OG_HASH_INPUTS
    .flatMap((inputPath) => collectHashInputFiles(rootDir, inputPath))
    .sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));

  for (const file of files) {
    const relative = path.relative(rootDir, file);
    hash.update(relative);
    hash.update("\0");
    if (fs.existsSync(file) && fs.statSync(file).isFile()) {
      hash.update(fs.readFileSync(file));
    } else {
      hash.update("<missing>");
    }
    hash.update("\0");
  }

  return hash.digest("hex").slice(0, 16);
}
