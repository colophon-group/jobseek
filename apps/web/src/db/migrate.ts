import dotenv from "dotenv";
dotenv.config({ path: ".env.local", quiet: true });
import { migrate } from "drizzle-orm/postgres-js/migrator";
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";

const unpooledUrl = process.env.DATABASE_URL_UNPOOLED;
const url = unpooledUrl ?? process.env.DATABASE_URL;
if (!url) {
  throw new Error("DATABASE_URL_UNPOOLED or DATABASE_URL must be set");
}
if (process.env.MIGRATION_REQUIRE_UNPOOLED === "true" && !unpooledUrl) {
  throw new Error(
    "MIGRATION_REQUIRE_UNPOOLED=true requires DATABASE_URL_UNPOOLED",
  );
}

const parsedUrl = new URL(url);
if (
  process.env.MIGRATION_REQUIRE_UNPOOLED === "true" &&
  parsedUrl.port === "6543"
) {
  throw new Error("Refusing to migrate through the Supabase transaction pooler");
}

const sql = postgres(url, {
  max: 1,
  prepare: false,
  connection: { application_name: "jobseek-web-migrations" },
});

async function main() {
  const reserved = await sql.reserve();
  const db = drizzle(reserved);
  let advisoryLockHeld = false;

  try {
    const [lock] = await reserved<{ acquired: boolean }[]>`
      SELECT pg_try_advisory_lock(
        hashtextextended('jobseek:web-schema-migrations', 0)
      ) AS acquired
    `;
    advisoryLockHeld = lock?.acquired === true;
    if (!advisoryLockHeld) {
      throw new Error("Another web schema migration is already running");
    }

    await reserved`SET lock_timeout = '10s'`;
    await reserved`SET statement_timeout = '10min'`;
    await reserved`SET idle_in_transaction_session_timeout = '2min'`;

    console.log("Running migrations with the web schema advisory lock...");
    await migrate(db, { migrationsFolder: "./drizzle" });
    console.log("Migrations complete.");
  } finally {
    try {
      if (advisoryLockHeld) {
        const [unlock] = await reserved<{ released: boolean }[]>`
          SELECT pg_advisory_unlock(
            hashtextextended('jobseek:web-schema-migrations', 0)
          ) AS released
        `;
        if (unlock?.released !== true) {
          console.error("Migration advisory lock release was not confirmed");
          process.exitCode = 1;
        }
      }
    } finally {
      await reserved.release();
      await sql.end();
    }
  }
}

void main().catch((err: unknown) => {
  console.error("Migration failed:", err);
  process.exitCode = 1;
});
