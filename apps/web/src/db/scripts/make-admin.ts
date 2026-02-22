import dotenv from "dotenv";
dotenv.config({ path: ".env.local" });
import { neon } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-http";
import { eq } from "drizzle-orm";
import { usersMeta } from "../schema";

const url = process.env.DATABASE_URL_UNPOOLED ?? process.env.DATABASE_URL;
if (!url) {
  throw new Error("DATABASE_URL_UNPOOLED or DATABASE_URL must be set");
}

const stackUserId = process.argv[2];
if (!stackUserId) {
  console.error("Usage: tsx src/db/scripts/make-admin.ts <stack_user_id>");
  process.exit(1);
}

const db = drizzle(neon(url));

async function main() {
  const existing = await db
    .select()
    .from(usersMeta)
    .where(eq(usersMeta.stackUserId, stackUserId));

  if (existing.length > 0) {
    await db
      .update(usersMeta)
      .set({ role: "admin", updatedAt: new Date() })
      .where(eq(usersMeta.stackUserId, stackUserId));
    console.log(`Updated existing user ${stackUserId} to admin.`);
  } else {
    await db.insert(usersMeta).values({
      stackUserId,
      role: "admin",
    });
    console.log(`Created admin user_meta for ${stackUserId}.`);
  }
}

main().catch((err) => {
  console.error("Failed:", err);
  process.exit(1);
});
