import { readFileSync } from "node:fs";
import type { TransactionSql } from "postgres";

export type CompanyDependencyPhase = "bridge" | "reference";
interface ForeignKeyLifecycle {
  table: string;
  column: string;
  owner: string;
  classification: "active_selection" | "retained_history";
  producer: string;
  replacementProducer: string;
  disposition: string;
  foreignKey: Record<CompanyDependencyPhase, { target: string; deleteAction: string }>;
}
interface SnapshotColumn { name: string; type: string; notNull: boolean }
export interface CompanyDependencyManifest {
  version: number;
  retiredProducers: Record<CompanyDependencyPhase, string[]>;
  foreignKeys: ForeignKeyLifecycle[];
  optionalForeignKeys: (ForeignKeyLifecycle & { tableShape: (SnapshotColumn & { default: string | null })[] })[];
  snapshot: {
    table: string; owner: string; producer: string; replacementProducer: string;
    disposition: string; columns: SnapshotColumn[];
  };
}
export const companyDependencyManifest = JSON.parse(readFileSync(
  new URL("./company-reference-dependencies.json", import.meta.url), "utf8",
)) as CompanyDependencyManifest;

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const identifier = (value: unknown): value is string => text(value) && /^[a-z_][a-z0-9_]*$/.test(value);
const requiredSnapshot: SnapshotColumn[] = [
  { name: "company_id", type: "uuid", notNull: true },
  { name: "company_name", type: "text", notNull: true },
  { name: "company_slug", type: "text", notNull: true },
  { name: "company_icon", type: "text", notNull: false },
];

/** Retiring a producer requires a checked replacement or explicit retained-history owner. */
export function assertCompanyDependencyLifecycle(manifest: CompanyDependencyManifest, phase: CompanyDependencyPhase) {
  assert(manifest.version === 1 && Array.isArray(manifest.foreignKeys), "Company dependency manifest version/shape differs");
  const retired = manifest.retiredProducers?.[phase];
  assert(Array.isArray(retired) && retired.every(identifier), "Company dependency retirement declarations absent");
  assert(Array.isArray(manifest.optionalForeignKeys), "Optional historical dependency declarations absent");
  const declared = [...manifest.foreignKeys, ...manifest.optionalForeignKeys];
  assert(new Set(declared.map(row => `${row.table}.${row.column}`)).size === declared.length,
    "Duplicate company dependency inventory entry");
  for (const row of declared) {
    assert(identifier(row.table) && identifier(row.column) && text(row.owner) && text(row.disposition)
      && identifier(row.producer) && identifier(row.replacementProducer), "Company dependency lifecycle owner/disposition absent");
    assert(row.classification === "active_selection" || row.classification === "retained_history", "Company dependency classification differs");
    const fk = row.foreignKey?.[phase];
    assert(fk && ["company", "company_reference"].includes(fk.target) && ["c", "n", "r"].includes(fk.deleteAction),
      "Company dependency phase disposition absent");
    if (row.classification === "active_selection" && (phase === "reference" || retired.includes(row.producer))) {
      assert(fk.target === "company_reference" && fk.deleteAction === "r"
        && row.replacementProducer === "web_reference_materializer" && !retired.includes(row.replacementProducer),
      "Retired producer retains an active company dependency without restrictive replacement lifecycle");
    }
    if (row.classification === "retained_history") {
      assert(row.replacementProducer === "retained_history" && fk.target === "company",
        "Retained legacy history requires its owned independent retirement disposition");
    }
  }
  const snapshot = manifest.snapshot;
  assert(snapshot?.table === "saved_job" && text(snapshot.owner) && text(snapshot.disposition)
    && snapshot.producer === "saved_job_snapshot_writer" && snapshot.replacementProducer === "self_contained_history"
    && !retired.includes(snapshot.producer) && Array.isArray(snapshot.columns), "Saved-job snapshot lifecycle absent");
  assert(JSON.stringify(snapshot.columns) === JSON.stringify(requiredSnapshot), "Saved-job independent company snapshot manifest differs");
}

/** Catalog-only queries: no customer rows, metadata values, or identifiers are emitted. */
export async function auditCompanyReferenceDependencies(tx: TransactionSql, phase: CompanyDependencyPhase) {
  assertCompanyDependencyLifecycle(companyDependencyManifest, phase);
  const presentOptional: typeof companyDependencyManifest.optionalForeignKeys = [];
  for (const row of companyDependencyManifest.optionalForeignKeys) {
    assert(row.classification === "retained_history" && Array.isArray(row.tableShape), "Optional dependency must declare retained historical table shape");
    const [relation] = await tx<{ kind: string; persistent: string }[]>`
      SELECT c.relkind::text AS kind, c.relpersistence::text AS persistent FROM pg_class c
      WHERE c.oid=to_regclass(${'public.' + row.table})`;
    if (!relation) continue;
    assert(relation.kind === "r" && relation.persistent === "p", "Optional historical dependency relation differs");
    const columns = await tx<(SnapshotColumn & { default: string | null })[]>`
      SELECT a.attname AS name, format_type(a.atttypid,a.atttypmod) AS type, a.attnotnull AS "notNull",
        pg_get_expr(d.adbin,d.adrelid) AS default
      FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
      WHERE a.attrelid=to_regclass(${'public.' + row.table}) AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum`;
    assert(JSON.stringify(columns) === JSON.stringify(row.tableShape), "Optional historical dependency exact table shape differs");
    const constraints = await tx<{ type: string; definition: string }[]>`
      SELECT contype::text AS type, pg_get_constraintdef(oid,true) AS definition FROM pg_constraint
      WHERE conrelid=to_regclass(${'public.' + row.table})`;
    assert(constraints.length === 2 && constraints.some(c => c.type === "p" && c.definition === "PRIMARY KEY (run_id)")
      && constraints.filter(c => c.type === "f").length === 1, "Optional historical dependency constraints differ");
    presentOptional.push(row);
  }
  const foreignKeys = await tx<{
    schema: string; table: string; columns: string[]; targetSchema: string; target: string;
    targetColumns: string[]; deleteAction: string; updateAction: string;
    validated: boolean; deferrable: boolean; deferred: boolean;
  }[]>`
    SELECT source_ns.nspname AS schema, source.relname AS table,
      ARRAY(SELECT a.attname FROM unnest(f.conkey) WITH ORDINALITY key(attnum, ord)
        JOIN pg_attribute a ON a.attrelid=f.conrelid AND a.attnum=key.attnum ORDER BY key.ord) AS columns,
      target_ns.nspname AS "targetSchema", target.relname AS target,
      ARRAY(SELECT a.attname FROM unnest(f.confkey) WITH ORDINALITY key(attnum, ord)
        JOIN pg_attribute a ON a.attrelid=f.confrelid AND a.attnum=key.attnum ORDER BY key.ord) AS "targetColumns",
      f.confdeltype::text AS "deleteAction", f.confupdtype::text AS "updateAction",
      f.convalidated AS validated, f.condeferrable AS deferrable, f.condeferred AS deferred
    FROM pg_constraint f
    JOIN pg_class source ON source.oid=f.conrelid JOIN pg_namespace source_ns ON source_ns.oid=source.relnamespace
    JOIN pg_class target ON target.oid=f.confrelid JOIN pg_namespace target_ns ON target_ns.oid=target.relnamespace
    WHERE f.contype='f' AND target_ns.nspname='public' AND target.relname IN ('company','company_reference')`;
  const expected = [...companyDependencyManifest.foreignKeys, ...presentOptional].map(row => ({
    schema: "public", table: row.table, columns: [row.column], targetSchema: "public",
    target: row.foreignKey[phase].target, targetColumns: ["id"], deleteAction: row.foreignKey[phase].deleteAction,
    updateAction: "a", validated: true, deferrable: false, deferred: false,
  }));
  const sorted = (rows: typeof expected) => rows.map(row => JSON.stringify(row)).sort();
  // Normalize object key order explicitly before comparing; constraint names
  // differ across historical deployments and are not lifecycle identity.
  const actual = foreignKeys.map(row => ({
    schema: row.schema, table: row.table, columns: row.columns, targetSchema: row.targetSchema,
    target: row.target, targetColumns: row.targetColumns, deleteAction: row.deleteAction,
    updateAction: row.updateAction, validated: row.validated, deferrable: row.deferrable, deferred: row.deferred,
  }));
  assert(JSON.stringify(sorted(actual)) === JSON.stringify(sorted(expected)), "Company dependency inventory/catalog drift");
  const columns = await tx<SnapshotColumn[]>`
    SELECT a.attname AS name, format_type(a.atttypid,a.atttypmod) AS type, a.attnotnull AS "notNull"
    FROM pg_attribute a WHERE a.attrelid=to_regclass('public.saved_job')
      AND a.attnum > 0 AND NOT a.attisdropped AND a.attname IN ('company_id','company_name','company_slug','company_icon')`;
  assert(JSON.stringify(columns.map(row => JSON.stringify(row)).sort()) === JSON.stringify(requiredSnapshot.map(row => JSON.stringify(row)).sort()),
    "Saved-job independent company snapshot columns differ");
  const snapshotForeignKeys = await tx`
    SELECT f.oid FROM pg_constraint f JOIN pg_attribute a ON a.attrelid=f.conrelid AND a.attnum=ANY(f.conkey)
    WHERE f.contype='f' AND f.conrelid=to_regclass('public.saved_job') AND a.attname='company_id'`;
  assert(snapshotForeignKeys.length === 0, "Saved-job company snapshot must remain independent of foreign keys");
  return { phase, foreignKeyCount: foreignKeys.length, lifecycleOwners: companyDependencyManifest.foreignKeys.length + companyDependencyManifest.optionalForeignKeys.length + 1, optionalForeignKeyCount: presentOptional.length, snapshot: "independent" };
}
