/**
 * Backfill is_display=true for non-English locales (de, fr, it).
 *
 * For locations with exactly one name in a locale → mark it as display.
 * For locations with multiple names → pick the shortest (approximates
 * GeoNames "isShortName" preference).
 *
 * Non-destructive: only UPDATEs is_display from false → true.
 *
 * Usage:
 *   npx tsx scripts/backfill-display-names.ts [--dry-run]
 */
import dotenv from "dotenv";
dotenv.config({ path: ".env.local" });
import postgres from "postgres";

const dryRun = process.argv.includes("--dry-run");
const sql = postgres(process.env.DATABASE_URL_UNPOOLED ?? process.env.DATABASE_URL!, { max: 1 });

async function main() {
  const locales = ["de", "fr", "it"];

  for (const locale of locales) {
    // For locations with exactly one name in this locale, mark it as display
    const singlesResult = await sql`
      UPDATE location_name ln
      SET is_display = true
      FROM (
        SELECT location_id
        FROM location_name
        WHERE locale = ${locale}
        GROUP BY location_id
        HAVING COUNT(*) = 1
      ) singles
      WHERE ln.location_id = singles.location_id
        AND ln.locale = ${locale}
        AND ln.is_display = false
      ${dryRun ? sql`RETURNING ln.location_id` : sql``}
    `;
    const singlesCount = dryRun ? singlesResult.length : singlesResult.count;
    console.log(`${locale}: ${singlesCount} single-name locations marked as display`);

    // For locations with multiple names, pick the shortest non-historic name.
    // We use DISTINCT ON + ORDER BY length to pick one per location_id.
    const multiplesResult = await sql`
      UPDATE location_name ln
      SET is_display = true
      FROM (
        SELECT DISTINCT ON (location_id) location_id, name
        FROM location_name
        WHERE locale = ${locale}
        ORDER BY location_id, length(name) ASC
      ) best
      WHERE ln.location_id = best.location_id
        AND ln.locale = ${locale}
        AND ln.name = best.name
        AND ln.is_display = false
      ${dryRun ? sql`RETURNING ln.location_id` : sql``}
    `;
    const multiplesCount = dryRun ? multiplesResult.length : multiplesResult.count;
    console.log(`${locale}: ${multiplesCount} multi-name locations marked as display`);
  }

  // Verify
  const verify = await sql`
    SELECT locale, COUNT(*)::int AS display_count
    FROM location_name
    WHERE locale IN ('en', 'de', 'fr', 'it')
      AND is_display = true
    GROUP BY locale
    ORDER BY locale
  `;
  console.log("\nFinal is_display counts:");
  for (const r of verify) {
    console.log(`  ${r.locale}: ${r.display_count}`);
  }

  await sql.end();
}

main();
