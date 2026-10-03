import { build } from "esbuild";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

async function main() {
const app = process.cwd();
const root = resolve(app, "../..");
const config = JSON.parse(readFileSync(resolve(root, "deploy/backups/web-postgresql/company-reference-rehearsal.json"), "utf8")) as {
  version: number; runtimeImage: string; migrations: { tag: string; createdAt: number; hash: string }[];
};
if (config.version !== 1 || !/^node:24-alpine@sha256:[a-f0-9]{64}$/.test(config.runtimeImage)
  || !config.migrations.length || config.migrations.some(row => !["0100_company_references", "0101_company_reference_selection_contract"].includes(row.tag))) {
  throw new Error("Invalid reviewed rehearsal allowlist/runtime");
}
const sourceRevision = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();
const sourceClean = execFileSync("git", ["status", "--porcelain", "--untracked-files=all"], { cwd: root, encoding: "utf8" }).trim() === "";
const out = resolve(root, "deploy/backups/web-postgresql/company-reference-bundle");
rmSync(out, { recursive: true, force: true }); mkdirSync(out, { recursive: true });
const result = await build({
  entryPoints: [resolve(app, "scripts/rehearse-company-reference.ts")],
  outfile: resolve(out, "rehearse.mjs"), bundle: true, platform: "node", target: "node24", format: "esm",
  tsconfig: resolve(app, "tsconfig.json"), metafile: true,
  alias: { "@jseek/mcp-server/public-api-contract": resolve(root, "packages/mcp-server/src/public-api-contract.ts") },
  banner: { js: "import { createRequire as rehearsalCreateRequire } from 'node:module'; const require = rehearsalCreateRequire(import.meta.url);" },
});
const journal = JSON.parse(readFileSync(resolve(app, "drizzle/meta/_journal.json"), "utf8")) as {
  entries: { tag: string; when: number }[];
};
const resources = new Set(["drizzle/meta/_journal.json", "scripts/company-reference-dependencies.json"]);
for (const migration of config.migrations) {
  const index = journal.entries.findIndex(row => row.tag === migration.tag);
  if (index < 1 || journal.entries[index].when !== migration.createdAt
    || createHash("sha256").update(readFileSync(resolve(app, `drizzle/${migration.tag}.sql`))).digest("hex") !== migration.hash) {
    throw new Error("Rehearsal SQL/journal differs from reviewed allowlist");
  }
  resources.add(`drizzle/${migration.tag}.sql`);
  resources.add(`drizzle/${journal.entries[index - 1].tag}.sql`);
}
const files: Record<string, string> = {};
for (const resource of [...resources].sort()) {
  const destination = resource.replace(/^scripts\//, "");
  mkdirSync(dirname(resolve(out, destination)), { recursive: true });
  copyFileSync(resolve(app, resource), resolve(out, destination));
  files[destination] = createHash("sha256").update(readFileSync(resolve(out, destination))).digest("hex");
}
files["rehearse.mjs"] = createHash("sha256").update(readFileSync(resolve(out, "rehearse.mjs"))).digest("hex");
const sources: Record<string, string> = {};
for (const input of Object.keys(result.metafile!.inputs).sort()) {
  const path = resolve(app, input);
  const relative = path.slice(root.length + 1);
  sources[relative] = createHash("sha256").update(readFileSync(path)).digest("hex");
}
for (const resource of [...resources, "package.json"]) sources[`apps/web/${resource}`]
  = createHash("sha256").update(readFileSync(resolve(app, resource))).digest("hex");
for (const path of ["apps/web/scripts/build-company-reference-rehearsal.ts", "deploy/backups/web-postgresql/company-reference-rehearsal.json"]) sources[path] = createHash("sha256").update(readFileSync(resolve(root, path))).digest("hex");
sources["pnpm-lock.yaml"] = createHash("sha256").update(readFileSync(resolve(root, "pnpm-lock.yaml"))).digest("hex");
writeFileSync(resolve(out, "manifest.json"), JSON.stringify({ ...config, sourceRevision, sourceClean, files, sources }, null, 2) + "\n");
console.log(JSON.stringify({ contract: "company_reference_rehearsal_bundle", sourceRevision,
  manifestSha256: createHash("sha256").update(readFileSync(resolve(out, "manifest.json"))).digest("hex"), runtimeImage: config.runtimeImage }));

}
void main().catch(() => { console.error("Rehearsal bundle build failed"); process.exitCode = 1; });
