import dotenv from "dotenv";
dotenv.config({ path: ".env.local" });
import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
import { eq } from "drizzle-orm";
import { user, subscription } from "../src/db/schema";
import { logExternalError } from "../src/lib/safe-external-error";

const url = process.env.DATABASE_URL_UNPOOLED ?? process.env.DATABASE_URL;
if (!url) {
  throw new Error("DATABASE_URL_UNPOOLED or DATABASE_URL must be set");
}

const sql = postgres(url);
const db = drizzle(sql);

const ADMIN_EMAIL = "colophongroup@gmail.com";

async function main() {
  // 1. Find user by email
  const [found] = await db
    .select({ id: user.id, name: user.name, email: user.email })
    .from(user)
    .where(eq(user.email, ADMIN_EMAIL))
    .limit(1);

  if (!found) {
    console.error(`No user found with email: ${ADMIN_EMAIL}`);
    process.exit(1);
  }

  console.log(`Found user: ${found.name} (${found.id})`);

  // 2. Upsert an unlimited subscription (the table has a unique index on userId)
  await db
    .insert(subscription)
    .values({
      userId: found.id,
      plan: "unlimited",
      status: "active",
      startsAt: new Date(),
      endsAt: new Date("2099-01-01"),
    })
    .onConflictDoUpdate({
      target: subscription.userId,
      set: {
        plan: "unlimited",
        status: "active",
        endsAt: new Date("2099-01-01"),
        updatedAt: new Date(),
      },
    });

  console.log(`Granted "unlimited" subscription to ${ADMIN_EMAIL}`);
  await sql.end();
}

main().catch((err) => {
  logExternalError("error", { service: "database", operation: "grant_admin_subscription" }, err);
  process.exit(1);
});
