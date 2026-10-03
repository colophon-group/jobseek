import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Sql } from "postgres";
import { normalizeWatchlistCompaniesForRead } from "../src/lib/services/watchlist-input";
const forbiddenControls = String.raw`[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]`;

export const COMPANY_REFERENCE_MIGRATION_TAG = "0100_company_references";
export type CompanyReferenceMode = "preflight" | "postflight" | "drift";
const migrationFolder = resolve(process.cwd(), "drizzle");
const journal = JSON.parse(readFileSync(resolve(migrationFolder, "meta/_journal.json"), "utf8")) as {
  entries: { tag: string; when: number }[];
};
const targetIndex = journal.entries.findIndex(entry => entry.tag === COMPANY_REFERENCE_MIGRATION_TAG);
if (targetIndex < 1) throw new Error("Company reference migration prerequisite missing");
const identity = (entry: { tag: string; when: number }) => ({
  tag: entry.tag, createdAt: entry.when,
  hash: createHash("sha256").update(readFileSync(resolve(migrationFolder, `${entry.tag}.sql`))).digest("hex"),
});
export const referenceMigrationIdentity = identity(journal.entries[targetIndex]!);
export const referencePrerequisiteIdentity = identity(journal.entries[targetIndex - 1]!);
const migrationSql = readFileSync(resolve(migrationFolder, `${COMPANY_REFERENCE_MIGRATION_TAG}.sql`), "utf8");
const expectedFunctionBody = migrationSql.match(/LANGUAGE plpgsql SET search_path = pg_catalog, public AS \$\$([\s\S]*?)\$\$/)?.[1]?.trim();

const expectedChecks: Record<string, string> = {
  company_reference_name_check: `CHECK (length(btrim(name)) > 0 AND length(name) <= 300 AND name !~ '${forbiddenControls}')`,
  company_reference_slug_check: "CHECK (length(btrim(slug)) > 0 AND length(slug) <= 100 AND slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$')",
  company_reference_icon_check: `CHECK (icon IS NULL OR length(icon) <= 2048 AND icon !~ '${forbiddenControls}')`,
  company_reference_source_check: "CHECK (source IN ('legacy_seed', 'typesense'))",
  company_reference_verification_check: "CHECK (source = 'legacy_seed' OR verified_at IS NOT NULL)",
};
// PostgreSQL renders IN as = ANY and adds casts/parentheses. Normalize only
// those mechanical differences; keep all operands, bounds and boolean operators.
export const normalizeReferenceCheck = (value: string) =>
  (value.match(/'(?:''|[^'])*'|[^']+/g) ?? []).map(token => token.startsWith("'") ? token : token
    .replace(/::text\[\]|::text/g, "")
    .replace(/=\s*ANY\s*\(ARRAY\[/g, "IN (").replace(/\]\)/g, ")")
    .replace(/\s/g, "")).join("");

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

export function validateCompanyReferenceRuntimeRole(role: string): string {
  if (!/^[a-z_][a-z0-9_]{0,62}$/.test(role) || ["anon", "authenticated", "jobseek_migration_auditor"].includes(role)) {
    throw new Error("Invalid audited company reference runtime role");
  }
  return role;
}

export async function auditCompanyReferences(sql: Sql, mode: CompanyReferenceMode, runtimeRole?: string) {
  if (runtimeRole !== undefined) validateCompanyReferenceRuntimeRole(runtimeRole);
  return sql.begin(async tx => {
    await tx`SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY`;
    await tx`SET LOCAL statement_timeout = '60s'`;
    const ledger = await tx<{ createdAt: string; hash: string }[]>`
      SELECT created_at::text AS "createdAt", hash FROM drizzle.__drizzle_migrations ORDER BY created_at DESC, id DESC`;
    const exact = (entry: typeof referenceMigrationIdentity) => ledger.filter(row => Number(row.createdAt) === entry.createdAt && row.hash === entry.hash).length;
    const unique = (entry: typeof referenceMigrationIdentity, count: number) =>
      exact(entry) === count && ledger.filter(row => Number(row.createdAt) === entry.createdAt || row.hash === entry.hash).length === count;
    const [relation] = await tx<{ oid: string | null; kind: string | null; persistent: string | null; rls: boolean | null }[]>`
      SELECT c.oid::text AS oid, c.relkind::text AS kind, c.relpersistence::text AS persistent, c.relrowsecurity AS rls
      FROM (SELECT to_regclass('public.company_reference') AS oid) r LEFT JOIN pg_class c ON c.oid = r.oid`;
    const phase = mode === "drift" ? (relation?.oid ? "postflight" : "preflight") : mode;
    assert(unique(referencePrerequisiteIdentity, 1), "Exact 0099 prerequisite identity absent or duplicated");
    assert(unique(referenceMigrationIdentity, phase === "preflight" ? 0 : 1), "0100 identity absent, duplicated or mismatched");
    if (mode !== "drift" || phase === "preflight") {
      const expected = phase === "preflight" ? referencePrerequisiteIdentity : referenceMigrationIdentity;
      assert(Number(ledger[0]?.createdAt) === expected.createdAt && ledger[0]?.hash === expected.hash, "Unexpected exact migration ledger head");
    }
    const [malformed] = await tx<{ count: number }[]>`
      SELECT count(*)::integer AS count FROM public.company
      WHERE name IS NULL OR length(btrim(name)) = 0 OR length(name) > 300
         OR slug IS NULL OR length(btrim(slug)) = 0 OR length(slug) > 100 OR length(icon) > 2048
         OR name ~ ${forbiddenControls} OR icon ~ ${forbiddenControls}
         OR slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'`;
    assert(malformed?.count === 0, `Seed refused: ${malformed?.count ?? "unknown"} malformed legacy company rows`);
    // Validate with the actual rendering normalizer in bounded private batches.
    // Publish only aggregate failures; names/IDs never enter verification artifacts.
    let nonrenderable = 0;
    const renderRows = phase === "preflight"
      ? tx`SELECT id, name, slug, icon FROM public.company ORDER BY id`
      : tx`SELECT id, name, slug, icon FROM public.company_reference ORDER BY id`;
    for await (const batch of renderRows.cursor(100)) {
      const rendered = normalizeWatchlistCompaniesForRead(batch);
      nonrenderable += batch.length - (rendered?.length ?? 0)
        + (rendered?.filter(row => "unavailable" in row && row.unavailable === true).length ?? 0);
    }
    assert(nonrenderable === 0, `Reference rendering contract refused: ${nonrenderable} nonrenderable companies`);
    const selectionForeignKeys = await tx<{ table: string; name: string; target: string; deleteAction: string; validated: boolean }[]>`
      SELECT conrelid::regclass::text AS "table", conname AS name, confrelid::regclass::text AS target,
        confdeltype::text AS "deleteAction", convalidated AS validated
      FROM pg_constraint WHERE contype = 'f' AND conrelid IN ('public.watchlist_company'::regclass, 'public.followed_company'::regclass)
        AND conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = conrelid AND attname = 'company_id' AND NOT attisdropped)]::smallint[]
      ORDER BY conrelid::regclass::text`;
    assert(selectionForeignKeys.length === 2 && selectionForeignKeys.every(fk => fk.target === "company" && fk.deleteAction === "c" && fk.validated), "Selection FK bridge contract differs");
    if (phase === "preflight") {
      assert(!relation?.oid, "company_reference exists before recorded expansion");
      assert((await tx`SELECT to_regprocedure('public.company_reference_from_legacy()') AS function`)[0]?.function === null, "Unrecorded legacy bridge function exists");
      return { mode, phase, status: "passed", migration: referenceMigrationIdentity, ledgerHead: ledger[0], malformed, nonrenderable, selectionForeignKeys };
    }
    assert(relation?.kind === "r" && relation.persistent === "p" && relation.rls, "Company reference must be a permanent RLS table");
    const columns = await tx<{ name: string; type: string; notNull: boolean; default: string | null }[]>`
      SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull AS "notNull", pg_get_expr(d.adbin, d.adrelid) AS default
      FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
      WHERE a.attrelid='public.company_reference'::regclass AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum`;
    const expectedColumns = [
      ["id", "uuid", true, null], ["name", "text", true, null], ["slug", "text", true, null], ["icon", "text", false, null],
      ["source", "text", true, null], ["verified_at", "timestamp with time zone", false, null],
      ["created_at", "timestamp with time zone", true, "now()"], ["updated_at", "timestamp with time zone", true, "now()"],
    ];
    assert(JSON.stringify(columns.map(c => [c.name, c.type, c.notNull, c.default])) === JSON.stringify(expectedColumns), "Company reference column/default/nullability drift");
    const checks = await tx<{ name: string; validated: boolean; definition: string }[]>`
      SELECT conname AS name, convalidated AS validated, pg_get_constraintdef(oid, true) AS definition
      FROM pg_constraint WHERE conrelid='public.company_reference'::regclass AND contype='c' ORDER BY conname`;
    assert(checks.length === 5 && checks.every(c => c.validated && expectedChecks[c.name] && normalizeReferenceCheck(c.definition) === normalizeReferenceCheck(expectedChecks[c.name]!)), "Company reference exact CHECK drift");
    const constraints = await tx<{ type: string; definition: string }[]>`
      SELECT contype::text AS type, pg_get_constraintdef(oid, true) AS definition FROM pg_constraint
      WHERE conrelid='public.company_reference'::regclass AND contype IN ('p', 'u', 'f', 'x')`;
    assert(constraints.length === 1 && constraints[0]?.type === "p" && constraints[0]?.definition === "PRIMARY KEY (id)", "Company reference identity/foreign-key constraint drift");
    const indexes = await tx<{ unique: boolean; valid: boolean; ready: boolean; definition: string }[]>`
      SELECT indisunique AS unique, indisvalid AS valid, indisready AS ready, pg_get_indexdef(indexrelid) AS definition
      FROM pg_index WHERE indrelid='public.company_reference'::regclass`;
    assert(indexes.length === 1 && indexes[0]?.unique && indexes[0]?.valid && indexes[0]?.ready && indexes[0]?.definition.endsWith("USING btree (id)"), "Company reference index drift (slug must not be unique)");
    const functions = await tx<{ body: string; securityDefiner: boolean; config: string[]; language: string }[]>`
      SELECT p.prosrc AS body, p.prosecdef AS "securityDefiner", p.proconfig AS config, l.lanname AS language
      FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=to_regprocedure('public.company_reference_from_legacy()')`;
    assert(functions.length === 1 && functions[0]?.body.trim() === expectedFunctionBody && !functions[0]?.securityDefiner
      && functions[0]?.language === "plpgsql" && JSON.stringify(functions[0]?.config) === JSON.stringify(["search_path=pg_catalog, public"]), "Legacy bridge function drift");
    const triggers = await tx<{ name: string; enabled: string; definition: string }[]>`
      SELECT tgname AS name, tgenabled::text AS enabled, pg_get_triggerdef(oid, true) AS definition
      FROM pg_trigger WHERE tgrelid='public.company'::regclass AND NOT tgisinternal AND tgname='company_reference_legacy_bridge'`;
    assert(triggers.length === 1 && triggers[0]?.enabled === "O" && triggers[0]?.definition === "CREATE TRIGGER company_reference_legacy_bridge AFTER INSERT OR UPDATE OF name, slug, icon ON company FOR EACH ROW EXECUTE FUNCTION company_reference_from_legacy()", "Legacy bridge trigger drift");
    const [coverage] = await tx<{ missingLegacy: number; mismatchedSeed: number; missingSelections: number }[]>`
      SELECT (SELECT count(*) FROM public.company c LEFT JOIN public.company_reference r ON r.id=c.id WHERE r.id IS NULL)::integer AS "missingLegacy",
      (SELECT count(*) FROM public.company c JOIN public.company_reference r ON r.id=c.id WHERE r.source='legacy_seed' AND (r.name IS DISTINCT FROM c.name OR r.slug IS DISTINCT FROM c.slug OR r.icon IS DISTINCT FROM c.icon))::integer AS "mismatchedSeed",
      (SELECT count(*) FROM (SELECT company_id FROM public.watchlist_company UNION ALL SELECT company_id FROM public.followed_company) s
        LEFT JOIN public.company_reference r ON r.id=s.company_id WHERE r.id IS NULL)::integer AS "missingSelections"`;
    assert(coverage?.missingLegacy === 0 && coverage.mismatchedSeed === 0 && coverage.missingSelections === 0, "Reference/selection coverage drift");
    const runtime = await tx<{ allowed: boolean }[]>`
      SELECT has_table_privilege(coalesce(${runtimeRole ?? null}, current_user::text), 'public.company_reference', 'SELECT')
        AND has_table_privilege(coalesce(${runtimeRole ?? null}, current_user::text), 'public.company_reference', 'INSERT')
        AND has_table_privilege(coalesce(${runtimeRole ?? null}, current_user::text), 'public.company_reference', 'UPDATE')
        AND has_table_privilege(coalesce(${runtimeRole ?? null}, current_user::text), 'public.company', 'INSERT')
        AND (r.rolsuper OR r.rolbypassrls OR c.relowner=r.oid)
        AND (NOT legacy.relrowsecurity OR r.rolsuper OR r.rolbypassrls OR legacy.relowner=r.oid) AS allowed
      FROM pg_roles r, pg_class c, pg_class legacy WHERE r.rolname=coalesce(${runtimeRole ?? null}, current_user::text)
        AND c.oid='public.company_reference'::regclass AND legacy.oid='public.company'::regclass`;
    assert(runtime[0]?.allowed, "Audited runtime role cannot read/write company references through RLS");
    const browser = await tx<{ role: string; allowed: boolean }[]>`
      SELECT rolname AS role, has_table_privilege(rolname, 'public.company_reference', 'SELECT,INSERT,UPDATE,DELETE') AS allowed
      FROM pg_roles WHERE rolname IN ('anon','authenticated')`;
    assert(browser.every(role => !role.allowed), "Browser role has company-reference table access");
    const auditor = await tx<{ select: boolean; writes: boolean; elevated: boolean; readOnlyDefault: boolean; directSelectOnly: boolean }[]>`
      SELECT has_table_privilege(r.oid, c.oid, 'SELECT') AS select,
        has_table_privilege(r.oid, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN') AS writes,
        (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls) AS elevated,
        coalesce('default_transaction_read_only=on'=ANY(r.rolconfig), false) AS "readOnlyDefault",
        (SELECT count(*)=1 AND bool_and(a.privilege_type='SELECT' AND NOT a.is_grantable)
          FROM aclexplode(coalesce(c.relacl, acldefault('r',c.relowner))) a WHERE a.grantee=r.oid) AS "directSelectOnly"
      FROM pg_roles r, pg_class c WHERE r.rolname='jobseek_migration_auditor' AND c.oid='public.company_reference'::regclass`;
    assert(auditor.every(a => a.select && !a.writes && !a.elevated && a.readOnlyDefault && a.directSelectOnly), "Migration auditor role/grant contract differs");
    const policies = await tx<{ name: string; command: string; permissive: boolean; roles: string[]; using: string | null; check: string | null }[]>`
      SELECT p.polname AS name, p.polcmd::text AS command, p.polpermissive AS permissive,
        ARRAY(SELECT rolname FROM pg_roles WHERE oid=ANY(p.polroles) ORDER BY rolname) AS roles,
        pg_get_expr(p.polqual,p.polrelid,true) AS using, pg_get_expr(p.polwithcheck,p.polrelid,true) AS check
      FROM pg_policy p WHERE p.polrelid='public.company_reference'::regclass ORDER BY p.polname`;
    assert(policies.length === auditor.length && policies.every(p => p.name==='company_reference_migration_auditor_select'
      && p.command==='r' && p.permissive && JSON.stringify(p.roles)===JSON.stringify(['jobseek_migration_auditor'])
      && p.using==='true' && p.check===null), "Migration auditor exact SELECT policy differs");
    return { mode, phase, status: "passed", migration: referenceMigrationIdentity, ledgerHead: ledger[0], relation, columns, checks, constraints, indexes,
      coverage, nonrenderable, runtime, browser, auditor, policies, selectionForeignKeys, compatibilityFunction: "exact", compatibilityTrigger: "exact" };
  });
}
