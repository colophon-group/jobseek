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

const lockSql = postgres(url, {
  max: 1,
  prepare: false,
  connect_timeout: 15,
  connection: { application_name: "jobseek-web-migration-lock" },
});
const migrationSql = postgres(url, {
  max: 1,
  prepare: false,
  connect_timeout: 15,
  connection: { application_name: "jobseek-web-migrations" },
});

async function main() {
  let lockConnection:
    | Awaited<ReturnType<typeof lockSql.reserve>>
    | undefined;
  let advisoryLockHeld = false;

  try {
    // Drizzle requires the root postgres.js client because migrations call
    // client.begin(). A reserved client has neither begin() nor options and
    // cannot be passed to drizzle(). Keep a separate reserved session solely
    // for the advisory lock while the one-connection migration pool owns DDL.
    lockConnection = await lockSql.reserve();
    const [lock] = await lockConnection<{ acquired: boolean }[]>`
      SELECT pg_try_advisory_lock(
        hashtextextended('jobseek:web-schema-migrations', 0)
      ) AS acquired
    `;
    advisoryLockHeld = lock?.acquired === true;
    if (!advisoryLockHeld) {
      throw new Error("Another web schema migration is already running");
    }

    // migrationSql has max=1, so these session settings and the Drizzle
    // transaction use the same physical connection.
    await migrationSql`SET lock_timeout = '10s'`;
    await migrationSql`SET statement_timeout = '10min'`;
    await migrationSql`SET idle_in_transaction_session_timeout = '2min'`;

    const db = drizzle(migrationSql);
    console.log("Running migrations with the web schema advisory lock...");
    await migrate(db, { migrationsFolder: "./drizzle" });
    console.log("Migrations complete.");
  } finally {
    try {
      if (lockConnection && advisoryLockHeld) {
        const [unlock] = await lockConnection<{ released: boolean }[]>`
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
      try {
        if (lockConnection) await lockConnection.release();
      } finally {
        await Promise.all([
          lockSql.end({ timeout: 5 }),
          migrationSql.end({ timeout: 5 }),
        ]);
      }
    }
  }
}

void main().catch((err: unknown) => {
  console.error("Migration failed:", err);
  process.exitCode = 1;
});
