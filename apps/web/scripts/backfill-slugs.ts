import dotenv from "dotenv";
dotenv.config({ path: ".env.local" });
import postgres from "postgres";

const dryRun = process.argv.includes("--dry-run");
const sql = postgres(process.env.DATABASE_URL_UNPOOLED ?? process.env.DATABASE_URL!, { max: 1 });

function slugify(str: string): string {
  return str
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

async function main() {
  // Fetch all locations with English display names and ancestor chain
  const rows = await sql`
    WITH RECURSIVE chain AS (
      SELECT l.id, l.type::text AS type, l.parent_id,
        ln.name,
        ARRAY[ln.name] AS names
      FROM location l
      JOIN location_name ln ON ln.location_id = l.id AND ln.locale = 'en' AND ln.is_display = true
      WHERE l.parent_id IS NULL
      UNION ALL
      SELECT l.id, l.type::text, l.parent_id,
        ln.name,
        c.names || ln.name
      FROM location l
      JOIN location_name ln ON ln.location_id = l.id AND ln.locale = 'en' AND ln.is_display = true
      JOIN chain c ON l.parent_id = c.id
    )
    SELECT id, type, name, names FROM chain
  `;

  console.log("Fetched", rows.length, "locations");

  // Count how many locations share each slugified name (for disambiguation)
  const nameCount = new Map<string, number>();
  for (const r of rows) {
    const key = slugify(r.name as string);
    nameCount.set(key, (nameCount.get(key) ?? 0) + 1);
  }

  // First pass: generate slugs
  const slugMap = new Map<number, string>();
  const slugBuckets = new Map<string, number[]>();

  for (const r of rows) {
    const id = r.id as number;
    const type = r.type as string;
    const name = r.name as string;
    const names = r.names as string[];
    const baseName = slugify(name);
    let slug: string;

    if (type === "macro" || type === "country") {
      slug = baseName;
    } else if (type === "region") {
      // region-country
      slug = baseName + "-" + slugify(names[0]);
    } else {
      // city
      if (nameCount.get(baseName) === 1) {
        // globally unique name
        slug = baseName;
      } else if (names.length >= 3) {
        // city-region-country
        slug =
          baseName +
          "-" +
          slugify(names[names.length - 2]) +
          "-" +
          slugify(names[0]);
      } else {
        // city directly under country
        slug = baseName + "-" + slugify(names[0]);
      }
    }

    slugMap.set(id, slug);
    const bucket = slugBuckets.get(slug);
    if (bucket) {
      bucket.push(id);
    } else {
      slugBuckets.set(slug, [id]);
    }
  }

  // Second pass: append location ID for remaining collisions
  for (const [slug, ids] of slugBuckets) {
    if (ids.length > 1) {
      for (const id of ids) {
        slugMap.set(id, slug + "-" + id);
      }
    }
  }

  // Verify uniqueness
  const finalSlugs = new Set(slugMap.values());
  console.log("Unique slugs:", finalSlugs.size, "/", slugMap.size);
  if (finalSlugs.size !== slugMap.size) {
    console.error("FATAL: slug uniqueness violated, aborting");
    process.exit(1);
  }

  // Samples
  const sampleNames = new Set(["Victoria", "Zurich", "EMEA", "Switzerland", "California", "Springfield", "London"]);
  for (const r of rows) {
    if (sampleNames.has(r.name as string)) {
      console.log(`  ${r.name} (${r.type}) → ${slugMap.get(r.id as number)}`);
    }
  }

  if (dryRun) {
    console.log("\n--dry-run: no writes performed");
    await sql.end();
    return;
  }

  // Batch UPDATE in chunks of 500
  const entries = [...slugMap.entries()];
  const BATCH = 500;
  let updated = 0;

  for (let i = 0; i < entries.length; i += BATCH) {
    const batch = entries.slice(i, i + BATCH);
    const ids = batch.map(([id]) => id);
    const slugs = batch.map(([, slug]) => slug);

    await sql`
      UPDATE location
      SET slug = data.slug
      FROM (SELECT unnest(${ids}::integer[]) AS id, unnest(${slugs}::text[]) AS slug) data
      WHERE location.id = data.id AND location.slug IS DISTINCT FROM data.slug
    `;

    updated += batch.length;
    if (updated % 5000 === 0 || i + BATCH >= entries.length) {
      console.log(`Updated ${updated} / ${entries.length}`);
    }
  }

  console.log("Done. Backfilled", updated, "slugs.");
  await sql.end();
}

main().catch((err) => {
  console.error("Failed:", err);
  process.exit(1);
});
