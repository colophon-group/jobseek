import { build } from "esbuild";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

async function main() {
const app = process.cwd();
const root = resolve(app, "../..");
const out = resolve(root, "deploy/ai-filter-refresh/bundle");
mkdirSync(out, { recursive: true });
const { runtimeImage } = JSON.parse(readFileSync(resolve(root, "deploy/ai-filter-refresh/worker-runtime.json"), "utf8")) as {runtimeImage:string};
if (!/^node:24-alpine@sha256:[a-f0-9]{64}$/.test(runtimeImage)) throw new Error("Unpinned worker runtime");
const result = await build({
  entryPoints: [resolve(app, "scripts/narrowed-worker.ts")],
  outfile: resolve(out, "worker.mjs"), bundle: true, platform: "node", target: "node24", format: "esm",
  define: { NARROWED_RUNTIME_IMAGE: JSON.stringify(runtimeImage) },
  conditions: ["react-server"], tsconfig: resolve(app, "tsconfig.json"), metafile: true,
  // The same matching engine with uncached, session-free data adapters on the host.
  alias: {
    "@/lib/services/watchlist-matcher": resolve(app, "src/lib/search/watchlist-matcher.ts"),
    "@/lib/services/search": resolve(app, "src/lib/search/matching-data.ts"),
  },
  plugins: [{ name: "forbid-web-runtime", setup(context) {
    context.onResolve({ filter: /^(next(?:\/|$)|react(?:\/|$)|workflow(?:\/|$)|@vercel\/)/ }, () => {
      throw new Error("A web runtime dependency entered the standalone worker");
    });
  } }],
  banner: { js: "import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);" },
});
const sourceRevision = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
const sourceClean = execFileSync("git", ["status", "--porcelain", "--untracked-files=all"], {encoding:"utf8"}).trim() === "";
const sources = Object.fromEntries(Object.keys(result.metafile!.inputs).sort().map(input => {
  const path = resolve(app, input);
  return [path.slice(root.length + 1), createHash("sha256").update(readFileSync(path)).digest("hex")];
}));
writeFileSync(resolve(out, "manifest.json"), JSON.stringify({ version: 1, sourceRevision, sourceClean, runtimeImage,
  workerSha256: createHash("sha256").update(readFileSync(resolve(out, "worker.mjs"))).digest("hex"), sources }, null, 2) + "\n");
console.info(JSON.stringify({ event: "narrowed_worker_built", sourceRevision, runtimeImage }));

}
void main().catch(() => { console.error("Narrowed worker build failed"); process.exitCode=1; });
