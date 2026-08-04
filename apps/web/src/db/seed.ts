import dotenv from "dotenv";
dotenv.config({ path: ".env.local" });
import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
import { company } from "./schema";
import { logExternalError } from "@/lib/safe-external-error";

const url = process.env.DATABASE_URL_UNPOOLED ?? process.env.DATABASE_URL;
if (!url) {
  throw new Error("DATABASE_URL_UNPOOLED or DATABASE_URL must be set");
}

const sql = postgres(url);
const db = drizzle(sql);

async function main() {
  console.log("Seeding database...");

  // --- Companies ---
  await db
    .insert(company)
    .values([
      {
        name: "Acme Corp",
        slug: "acme-corp",
        website: "https://acme.example.com",
        description: "A global leader in innovative solutions.",
      },
      {
        name: "Globex Corporation",
        slug: "globex-corporation",
        website: "https://globex.example.com",
        description: "Pioneering the future of technology.",
      },
      {
        name: "Initech",
        slug: "initech",
        website: "https://initech.example.com",
        description: "Enterprise software solutions.",
      },
    ])
    .onConflictDoNothing();

  // --- Subscriptions (placeholder user IDs — no real users in seed) ---
  // Skipped: subscriptions reference user.id via FK, so seeding
  // requires real Better Auth users. Create subscriptions after sign-up.

  console.log("Seed complete.");
  await sql.end();
}

main().catch((err) => {
  logExternalError("error", { service: "database", operation: "seed" }, err);
  process.exit(1);
});
