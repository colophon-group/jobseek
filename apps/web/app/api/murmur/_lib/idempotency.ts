/**
 * Idempotency helpers for the Murmur webhook accept handler.
 *
 * Murmur sends `Idempotency-Key: <run_id>` (per DESIGN.md §4.1) and
 * retries non-2xx responses once after 30s. The accept handler must
 * therefore:
 *
 *   1. Hash the canonical-JSON form of the body so re-fires can be
 *      classified as "same body" vs "different body".
 *   2. Look up `(run_id)` in the `murmur_accept_log` ledger.
 *   3. Decide one of three outcomes:
 *        - `fresh`         — never seen this run_id; proceed to apply.
 *        - `already`       — same hash as the existing row; idempotent
 *                            success, skip apply, return
 *                            `applied: false, reason: "already_applied"`.
 *        - `body_mismatch` — different hash; the first body is the
 *                            source of truth, log a warning and return
 *                            `applied: false, reason: "body_mismatch"`.
 *
 * The ledger insert and the catalog write are wrapped in the same
 * transaction so a crash between them never leaves a half-applied
 * state.
 *
 * @see colophon-group/jobseek#2763
 */

import { createHash } from "node:crypto";

/**
 * Stable, length-prefixed canonical form of a JSON value. Two bodies
 * that decode to the same JS object always produce the same string;
 * key order and whitespace differences are removed.
 *
 * The output is fed directly into SHA-256.
 *
 * NOTE: this is NOT RFC 8785 (JCS). It is a small homegrown
 * canonicaliser sufficient for "same-decoded-JS-object → same hash" on
 * `final_output` payloads. It does not reproduce JCS number formatting
 * (e.g. trailing-zero stripping, ECMA-262 7.1.12.1 step rules) and is
 * not interoperable with JCS implementations on either end.
 */
export function canonicalizeJson(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    return JSON.stringify(value);
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map(canonicalizeJson).join(",")}]`;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries
      .map(([k, v]) => `${JSON.stringify(k)}:${canonicalizeJson(v)}`)
      .join(",")}}`;
  }
  // undefined / function / symbol — JSON drops these. Mirror that.
  return "null";
}

/** Hex SHA-256 of the canonicalised JSON form. */
export function sha256Canonical(value: unknown): string {
  return createHash("sha256").update(canonicalizeJson(value)).digest("hex");
}

/** Outcome of classifying an inbound run_id + body against the ledger. */
export type LedgerCheck =
  | { readonly status: "fresh" }
  | { readonly status: "already_applied"; readonly companyId: string | null }
  | { readonly status: "body_mismatch"; readonly companyId: string | null };

/**
 * Read-side ledger probe. Implementations consult
 * `murmur_accept_log` and return one of the three statuses above.
 *
 * Tests provide a stub that returns whichever status the case wants.
 */
export type LedgerReader = (
  runId: string,
  bodyHash: string,
) => Promise<LedgerCheck>;

/**
 * Write-side ledger commit. Inserts the row alongside the catalog
 * write inside the same transaction. Throws on UNIQUE-constraint
 * violation (the caller upgrades that to `already_applied`).
 */
export type LedgerWriter = (entry: {
  readonly runId: string;
  readonly bodyHash: string;
  readonly companyId: string | null;
  readonly boardCount: number;
  readonly target: string;
}) => Promise<void>;

/**
 * Resolve the active backend the catalog writer will use. Mirrors
 * `resolveCatalogTarget()` in `catalog.ts` — duplicated here to keep
 * `idempotency.ts` independent (no circular import).
 */
function resolveActiveTarget(): "postgres" | "csv" {
  const v = process.env.MURMUR_ACCEPT_TARGET?.trim().toLowerCase();
  if (v === "csv") return "csv";
  return "postgres";
}

/**
 * Default ledger reader. Hits the durable backend (`murmur_accept_log`
 * for Postgres; the small `murmur_accept_log.csv` sidecar for the CSV
 * target) and classifies the inbound run_id + body hash:
 *
 *   - row absent             → `fresh`
 *   - row present, hash same → `already_applied`
 *   - row present, hash diff → `body_mismatch`
 *
 * The CSV path keeps a parallel `<csv_dir>/murmur_accept_log.csv` with
 * columns `run_id,body_sha256,target,applied_at` so cold-start CSV
 * idempotency works without a database. For the Postgres path we read
 * `murmur_accept_log` straight.
 *
 * On any I/O failure the reader surfaces `fresh` and logs — the route
 * still falls back to the in-process Map and the Postgres UNIQUE
 * constraint, so a single read failure cannot turn a true replay into
 * a double-apply.
 */
export const defaultLedgerReader: LedgerReader = async (runId, bodyHash) => {
  const target = resolveActiveTarget();
  if (target === "csv") {
    return readLedgerCsv(runId, bodyHash);
  }
  return readLedgerPostgres(runId, bodyHash);
};

async function readLedgerPostgres(
  runId: string,
  bodyHash: string,
): Promise<LedgerCheck> {
  try {
    const { db } = await import("@/db");
    const { murmurAcceptLog } = await import("@/db/schema");
    const { eq } = await import("drizzle-orm");
    const rows = await db
      .select({
        bodySha256: murmurAcceptLog.bodySha256,
        companyId: murmurAcceptLog.companyId,
      })
      .from(murmurAcceptLog)
      .where(eq(murmurAcceptLog.runId, runId))
      .limit(1);
    if (rows.length === 0 || !rows[0]) {
      return { status: "fresh" };
    }
    const row = rows[0];
    if (row.bodySha256 === bodyHash) {
      return { status: "already_applied", companyId: row.companyId ?? null };
    }
    return { status: "body_mismatch", companyId: row.companyId ?? null };
  } catch (err) {
    console.error(
      `[murmur ledger] postgres read failed for run_id=${runId}: ${(err as Error).message}`,
    );
    return { status: "fresh" };
  }
}

const CSV_DIR_ENV = "MURMUR_ACCEPT_CSV_DIR";
const LEDGER_CSV_FILENAME = "murmur_accept_log.csv";

async function readLedgerCsv(
  runId: string,
  bodyHash: string,
): Promise<LedgerCheck> {
  // Lazy-load fs/path so this module stays cheap when the route boots.
  const path = await import("node:path");
  const fs = await import("node:fs/promises");
  const dir =
    process.env[CSV_DIR_ENV] ??
    path.resolve(process.cwd(), "../crawler/data");
  const ledgerPath = path.join(dir, LEDGER_CSV_FILENAME);
  let raw: string;
  try {
    raw = await fs.readFile(ledgerPath, "utf8");
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      return { status: "fresh" };
    }
    console.error(
      `[murmur ledger] csv read failed for run_id=${runId}: ${(err as Error).message}`,
    );
    return { status: "fresh" };
  }
  const lines = raw.split(/\r?\n/);
  // Scan from the end — the most recent entry wins (so the same run_id
  // is classified against its first write, but a slightly newer line
  // would not lose to a stale one).
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (line.length === 0) continue;
    // Format: run_id,body_sha256,target,applied_at
    const fields = parseCsvLine(line);
    if (fields.length < 2) continue;
    if (fields[0] !== runId) continue;
    if (fields[1] === bodyHash) {
      return { status: "already_applied", companyId: null };
    }
    return { status: "body_mismatch", companyId: null };
  }
  return { status: "fresh" };
}

/**
 * Append a `(run_id,body_sha256,target,applied_at)` row to the CSV
 * ledger sidecar. Used by `applyCsv` so cold-start replays in CSV mode
 * survive a process restart.
 */
export async function appendLedgerCsv(entry: {
  readonly runId: string;
  readonly bodyHash: string;
  readonly target: string;
}): Promise<void> {
  const path = await import("node:path");
  const fs = await import("node:fs/promises");
  const dir =
    process.env[CSV_DIR_ENV] ??
    path.resolve(process.cwd(), "../crawler/data");
  await fs.mkdir(dir, { recursive: true });
  const ledgerPath = path.join(dir, LEDGER_CSV_FILENAME);
  const row =
    csvJoin([entry.runId, entry.bodyHash, entry.target, new Date().toISOString()]) +
    "\n";
  await fs.appendFile(ledgerPath, row, "utf8");
}

function csvJoin(fields: readonly string[]): string {
  return fields.map(csvField).join(",");
}

function csvField(s: string): string {
  if (/[",\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

/**
 * Minimal CSV-line parser — covers the exact subset `appendLedgerCsv`
 * produces (quoted fields with `""` escapes, otherwise comma-split).
 */
function parseCsvLine(line: string): string[] {
  const out: string[] = [];
  let i = 0;
  while (i < line.length) {
    if (line[i] === '"') {
      // quoted field
      let v = "";
      i += 1;
      while (i < line.length) {
        if (line[i] === '"') {
          if (line[i + 1] === '"') {
            v += '"';
            i += 2;
            continue;
          }
          i += 1;
          break;
        }
        v += line[i];
        i += 1;
      }
      out.push(v);
      if (line[i] === ",") i += 1;
    } else {
      let v = "";
      while (i < line.length && line[i] !== ",") {
        v += line[i];
        i += 1;
      }
      out.push(v);
      if (line[i] === ",") i += 1;
    }
  }
  return out;
}

/**
 * Mutable holder for the active ledger reader (mirrors
 * `ApplyCatalogHolder` / `RerunHolder`). Tests overwrite `current` to
 * stub the durable read; production code never reassigns it away from
 * `defaultLedgerReader`.
 */
export const LedgerReaderHolder: { current: LedgerReader } = {
  current: defaultLedgerReader,
};

/** Convenience pass-through used by the route. */
export function readLedger(
  runId: string,
  bodyHash: string,
): Promise<LedgerCheck> {
  return LedgerReaderHolder.current(runId, bodyHash);
}
