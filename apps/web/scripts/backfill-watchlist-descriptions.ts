import dotenv from "dotenv";
dotenv.config({ path: ".env.local" });
import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
import { eq } from "drizzle-orm";
import { user, watchlist, watchlistCompany, company } from "../src/db/schema";

const url = process.env.DATABASE_URL_UNPOOLED ?? process.env.DATABASE_URL;
if (!url) {
  throw new Error("DATABASE_URL_UNPOOLED or DATABASE_URL must be set");
}

const sql = postgres(url);
const db = drizzle(sql);

const ADMIN_EMAIL = "colophongroup@gmail.com";

type WatchlistFilters = {
  keywords?: string[];
  locationSlugs?: string[];
  occupationSlugs?: string[];
  senioritySlugs?: string[];
  technologySlugs?: string[];
  salaryMin?: number;
  salaryMax?: number;
  salaryCurrency?: string;
  experienceMin?: number;
  experienceMax?: number;
  anyCompany?: boolean;
};

function slugToLabel(slug: string): string {
  return slug
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function generateDescription(
  title: string,
  companyNames: string[],
  filters: WatchlistFilters,
): string {
  const parts: string[] = [];

  // Companies
  if (companyNames.length > 0) {
    const shown = companyNames.slice(0, 4);
    const rest = companyNames.length - shown.length;
    const companyList =
      rest > 0
        ? `${shown.join(", ")} and ${rest} more`
        : shown.length > 1
          ? `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}`
          : shown[0];
    parts.push(`Track open positions at ${companyList}`);
  }

  // Filters
  const filterFragments: string[] = [];

  if (filters.occupationSlugs?.length) {
    const labels = filters.occupationSlugs.slice(0, 3).map(slugToLabel);
    filterFragments.push(labels.join(", "));
  }

  if (filters.senioritySlugs?.length) {
    const labels = filters.senioritySlugs.slice(0, 2).map(slugToLabel);
    filterFragments.push(labels.join(", "));
  }

  if (filters.locationSlugs?.length) {
    const labels = filters.locationSlugs.slice(0, 3).map(slugToLabel);
    filterFragments.push(`in ${labels.join(", ")}`);
  }

  if (filters.technologySlugs?.length) {
    const labels = filters.technologySlugs.slice(0, 3).map(slugToLabel);
    filterFragments.push(`using ${labels.join(", ")}`);
  }

  if (filterFragments.length > 0) {
    if (parts.length > 0) {
      parts.push(`Filtered by ${filterFragments.join(" · ")}.`);
    } else {
      parts.push(`${filterFragments.join(" · ")} jobs.`);
    }
  }

  if (parts.length === 0) {
    // Fallback: derive from title
    return `Curated job watchlist: ${title}.`;
  }

  // Join and ensure it fits in ~200 chars
  let result = parts.join(". ");
  if (!result.endsWith(".")) result += ".";
  if (result.length > 200) {
    result = result.slice(0, 197) + "...";
  }
  return result;
}

async function main() {
  // 1. Find admin user
  const [found] = await db
    .select({ id: user.id, name: user.name })
    .from(user)
    .where(eq(user.email, ADMIN_EMAIL))
    .limit(1);

  if (!found) {
    console.error(`No user found with email: ${ADMIN_EMAIL}`);
    process.exit(1);
  }

  console.log(`Found user: ${found.name} (${found.id})`);

  // 2. Fetch all their watchlists
  const watchlists = await db
    .select({
      id: watchlist.id,
      title: watchlist.title,
      description: watchlist.description,
      filters: watchlist.filters,
    })
    .from(watchlist)
    .where(eq(watchlist.userId, found.id));

  console.log(`Found ${watchlists.length} watchlists`);

  let updated = 0;
  for (const wl of watchlists) {
    if (wl.description) {
      console.log(`  SKIP "${wl.title}" — already has description`);
      continue;
    }

    // Fetch company names for this watchlist
    const companies = await db
      .select({ name: company.name })
      .from(watchlistCompany)
      .innerJoin(company, eq(watchlistCompany.companyId, company.id))
      .where(eq(watchlistCompany.watchlistId, wl.id))
      .orderBy(company.name);

    const companyNames = companies.map((c) => c.name);
    const filters = (wl.filters ?? {}) as WatchlistFilters;

    const description = generateDescription(wl.title, companyNames, filters);

    await db
      .update(watchlist)
      .set({ description })
      .where(eq(watchlist.id, wl.id));

    console.log(`  SET "${wl.title}" → ${description}`);
    updated++;
  }

  console.log(`\nDone. Updated ${updated} of ${watchlists.length} watchlists.`);
  await sql.end();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
