import dotenv from "dotenv";
dotenv.config({ path: ".env.local" });
import { drizzle } from "drizzle-orm/postgres-js";
import { sql } from "drizzle-orm";
import postgres from "postgres";
import { usernameFromEmail, withRandomSuffix, isReservedUsername } from "../src/lib/username";

const url = process.env.DATABASE_URL_UNPOOLED ?? process.env.DATABASE_URL;
if (!url) {
  throw new Error("DATABASE_URL_UNPOOLED or DATABASE_URL must be set");
}

const pg = postgres(url, { max: 1 });
const db = drizzle(pg);

async function main() {
  const users = await db.execute(
    sql`SELECT id, email FROM "user" WHERE username IS NULL`,
  );

  console.log(`Found ${users.length} users without username`);

  let updated = 0;
  for (const user of users) {
    const email = user.email as string;
    const base = usernameFromEmail(email);
    let candidate = base;

    for (let i = 0; i < 5; i++) {
      if (isReservedUsername(candidate)) {
        candidate = withRandomSuffix(base);
        continue;
      }
      const existing = await db.execute(
        sql`SELECT 1 FROM "user" WHERE username = ${candidate} LIMIT 1`,
      );
      if (!existing.length) break;
      candidate = withRandomSuffix(base);
    }

    await db.execute(
      sql`UPDATE "user" SET username = ${candidate}, display_username = ${candidate} WHERE id = ${user.id as string}`,
    );
    console.log(`  ${email} -> ${candidate}`);
    updated++;
  }

  console.log(`Done. Updated ${updated} users.`);
  await pg.end();
}

main().catch((err) => {
  console.error("Backfill failed:", err);
  process.exit(1);
});
