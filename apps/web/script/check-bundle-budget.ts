import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const chunksRoot = path.join(appRoot, ".next", "static", "chunks");
const serverAppRoot = path.join(appRoot, ".next", "server", "app");

const maxChunkGzipKb = Number(process.env.BUNDLE_MAX_CHUNK_GZIP_KB ?? "325");
const maxTotalGzipKb = Number(process.env.BUNDLE_MAX_TOTAL_GZIP_KB ?? "1400");

type ChunkSize = {
  file: string;
  rawBytes: number;
  gzipBytes: number;
};

function walk(dir: string): string[] {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  return entries.flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) return walk(fullPath);
    return entry.isFile() && entry.name.endsWith(".js") ? [fullPath] : [];
  });
}

function walkPageTraces(dir: string): string[] {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  return entries.flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) return walkPageTraces(fullPath);
    return entry.isFile() && entry.name === "page.js.nft.json"
      ? [fullPath]
      : [];
  });
}

function formatKb(bytes: number): string {
  return `${(bytes / 1024).toFixed(1)} KB`;
}

if (!fs.existsSync(chunksRoot)) {
  console.error(`Bundle budget failed: ${chunksRoot} does not exist. Run next build first.`);
  process.exit(1);
}
if (!fs.existsSync(serverAppRoot)) {
  console.error(
    `Bundle budget failed: ${serverAppRoot} does not exist. Run next build first.`,
  );
  process.exit(1);
}

const chunks: ChunkSize[] = walk(chunksRoot)
  .map((file) => {
    const content = fs.readFileSync(file);
    return {
      file: path.relative(appRoot, file),
      rawBytes: content.byteLength,
      gzipBytes: zlib.gzipSync(content).byteLength,
    };
  })
  .sort((a, b) => b.gzipBytes - a.gzipBytes);

const totalGzipBytes = chunks.reduce((total, chunk) => total + chunk.gzipBytes, 0);
const maxChunkBytes = maxChunkGzipKb * 1024;
const maxTotalBytes = maxTotalGzipKb * 1024;
const oversized = chunks.filter((chunk) => chunk.gzipBytes > maxChunkBytes);

console.log("Largest JS chunks by gzip size:");
for (const chunk of chunks.slice(0, 10)) {
  console.log(`- ${chunk.file}: gzip ${formatKb(chunk.gzipBytes)}, raw ${formatKb(chunk.rawBytes)}`);
}
console.log(`Total static JS gzip: ${formatKb(totalGzipBytes)}`);

if (oversized.length > 0) {
  console.error(
    `Bundle budget failed: ${oversized.length} chunk(s) exceed ${maxChunkGzipKb} KB gzip.`,
  );
  for (const chunk of oversized) {
    console.error(`- ${chunk.file}: ${formatKb(chunk.gzipBytes)}`);
  }
  process.exit(1);
}

if (totalGzipBytes > maxTotalBytes) {
  console.error(
    `Bundle budget failed: total static JS gzip ${formatKb(totalGzipBytes)} exceeds ${maxTotalGzipKb} KB.`,
  );
  process.exit(1);
}

// A file-convention opengraph-image sibling can silently merge the entire
// Satori/Resvg/WASM renderer into its ordinary page Function trace. That
// inflated several hot traces by 4.4 MB and raised cold-start CPU without any
// page code importing next/og directly. Dynamic OG renderers must live under
// isolated `/og/*` route handlers; deterministic cards belong in R2.
const pageTraces = walkPageTraces(serverAppRoot);
const ogContaminatedTraces = pageTraces.filter((traceFile) => {
  const trace = JSON.parse(fs.readFileSync(traceFile, "utf8")) as {
    files?: unknown;
  };
  return Array.isArray(trace.files) && trace.files.some((file) =>
    typeof file === "string" && (
      file.includes("/next/dist/compiled/@vercel/og/") ||
      file.endsWith("/resvg.wasm")
    )
  );
});

console.log(`Inspected ${pageTraces.length} ordinary server page traces for OG payloads.`);
if (ogContaminatedTraces.length > 0) {
  console.error(
    "Bundle budget failed: next/og payload leaked into ordinary page traces.",
  );
  for (const traceFile of ogContaminatedTraces) {
    console.error(`- ${path.relative(appRoot, traceFile)}`);
  }
  process.exit(1);
}

console.log("Bundle budget passed");
