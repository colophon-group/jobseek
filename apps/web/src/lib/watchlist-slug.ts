import "server-only";

import { eq, and, sql } from "drizzle-orm";
import { db } from "@/db";
import { watchlist } from "@/db/schema";

export function slugifyTitle(title: string): string {
  return title
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/\+/g, "plus")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}

export async function generateUniqueSlug(
  userId: string,
  title: string,
): Promise<string> {
  const base = slugifyTitle(title) || "watchlist";

  const rows = await db
    .select({ slug: watchlist.slug })
    .from(watchlist)
    .where(
      and(
        eq(watchlist.userId, userId),
        sql`${watchlist.slug} LIKE ${base + "%"}`,
      ),
    );

  const existing = new Set(rows.map((r) => r.slug));
  if (!existing.has(base)) return base;

  let i = 2;
  while (existing.has(`${base}-${i}`)) i++;
  return `${base}-${i}`;
}
