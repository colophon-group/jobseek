/**
 * Insert one missing web company reference from reviewed registry data.
 * This does not restore crawler mirroring or change existing company rows.
 *
 * pnpm exec tsx scripts/repair-company-reference.ts --slug flyability \
 *   --expected-id <canonical UUID> --env-file /absolute/path/.env.local
 * Default: read-only dry run. Add --apply to insert, or --check to require
 * the reference already exists. The Typesense identity must match every run.
 */
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { parse } from "csv-parse/sync";
import dotenv from "dotenv";
import postgres from "postgres";

async function main() {
  const { values } = parseArgs({
    options: {
      slug: { type: "string" },
      "expected-id": { type: "string" },
      "env-file": { type: "string" },
      apply: { type: "boolean", default: false },
      check: { type: "boolean", default: false },
    },
  });
  const slug = values.slug ?? "";
  const id = values["expected-id"] ?? "";
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)
    || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id)
    || (values.apply && values.check)) throw new Error("Invalid arguments");
  const env = values["env-file"]
    ? { ...process.env, ...dotenv.parse(await readFile(values["env-file"])) }
    : process.env;
  const databaseUrl = env.DATABASE_URL;
  if (!databaseUrl || !env.TYPESENSE_HOST || !env.TYPESENSE_SEARCH_KEY)
    throw new Error("Missing database or search configuration");

  const csvPath = resolve(dirname(fileURLToPath(import.meta.url)), "../../crawler/data/companies.csv");
  const records = parse(await readFile(csvPath, "utf8"), {
    columns: true, bom: true, skip_empty_lines: true,
  }) as Record<string, string>[];
  const matches = records.filter((row) => row.slug === slug);
  if (matches.length !== 1) throw new Error("Registry must contain exactly one matching company");
  const row = matches[0];
  const optional = (field: string) => row[field]?.trim() || null;
  const integer = (field: string) => {
    const value = optional(field);
    if (value === null) return null;
    if (!/^\d+$/.test(value)) throw new Error("Invalid registry integer");
    return Number(value);
  };
  const extras: unknown = JSON.parse(row.extras || "{}");
  if (!extras || typeof extras !== "object" || Array.isArray(extras))
    throw new Error("Invalid registry extras");
  if (!row.name || ![null, "wordmark", "wordmark+icon", "icon"].includes(optional("logo_type")))
    throw new Error("Invalid registry metadata");
  const industry = integer("industry");
  const employees = integer("employee_count_range");
  const founded = integer("founded_year");

  const searchUrl = new URL(`${env.TYPESENSE_PROTOCOL || "https"}://${env.TYPESENSE_HOST}:${env.TYPESENSE_PORT || "443"}/collections/company/documents/search`);
  searchUrl.search = new URLSearchParams({ q: "*", filter_by: `slug:=${slug}`, per_page: "2" }).toString();
  const response = await fetch(searchUrl, {
    headers: { "X-TYPESENSE-API-KEY": env.TYPESENSE_SEARCH_KEY },
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new Error("Canonical search lookup failed");
  const data = await response.json() as { found?: number; hits?: { document: { id: string; slug: string } }[] };
  if (data.found !== 1 || data.hits?.length !== 1
    || data.hits[0].document.id !== id || data.hits[0].document.slug !== slug)
    throw new Error("Canonical search identity does not match requested repair");

  const sql = postgres(databaseUrl, { max: 1, prepare: false, connect_timeout: 10 });
  try {
    const status = await sql.begin(values.apply ? "isolation level serializable" : "read only", async (tx) => {
      await tx`SET LOCAL statement_timeout = '15s'`;
      await tx`SET LOCAL lock_timeout = '5s'`;
      const existing = await tx<{ id: string; slug: string }[]>`
        SELECT id::text, slug FROM company WHERE id = ${id}::uuid OR slug = ${slug}
      `;
      if (existing.length) {
        if (existing.length !== 1 || existing[0].id !== id || existing[0].slug !== slug)
          throw new Error("Existing company identity conflicts; no changes made");
        return "already_present";
      }
      if (values.check) throw new Error("Expected company reference is missing");
      if (industry !== null) {
        const industries = await tx`SELECT id FROM industry WHERE id = ${industry}`;
        if (industries.length !== 1) throw new Error("Referenced industry is missing");
      }
      if (!values.apply) return "would_insert";
      const inserted = await tx<{ id: string; slug: string }[]>`
        INSERT INTO company (id, slug, name, website, logo, icon, logo_type,
          industry, employee_count_range, founded_year, extras)
        VALUES (${id}::uuid, ${slug}, ${row.name}, ${optional("website")},
          ${optional("logo_url")}, ${optional("icon_url")}, ${optional("logo_type")},
          ${industry}, ${employees}, ${founded}, ${tx.json(JSON.parse(row.extras || "{}"))})
        RETURNING id::text, slug
      `;
      if (inserted.length !== 1 || inserted[0].id !== id || inserted[0].slug !== slug)
        throw new Error("Inserted company identity failed verification");
      const [verified] = await tx<{ valid: boolean }[]>`
        SELECT jsonb_typeof(extras) = 'object'
          AND extras = ${tx.json(JSON.parse(row.extras || "{}"))}::jsonb AS valid
        FROM company WHERE id = ${id}::uuid
      `;
      if (!verified?.valid) throw new Error("Inserted company metadata failed verification");
      return "inserted";
    });
    console.log(JSON.stringify({ status, slug, id, name: row.name,
      source: "apps/crawler/data/companies.csv", canonicalIdentity: "Typesense company", apply: values.apply }));
  } finally {
    await sql.end();
  }
}

main().catch(() => {
  // Database and HTTP errors can embed credentials or connection details.
  console.error("Company reference repair failed; no secrets logged");
  process.exitCode = 1;
});
