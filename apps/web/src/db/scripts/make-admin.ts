import dotenv from "dotenv";
dotenv.config({ path: ".env.local" });
import { neon } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-http";
import { eq } from "drizzle-orm";
import { user } from "../schema";

const url = process.env.DATABASE_URL_UNPOOLED ?? process.env.DATABASE_URL;
if (!url) {
  throw new Error("DATABASE_URL_UNPOOLED or DATABASE_URL must be set");
}

const userId = process.argv[2];
if (!userId) {
  console.error("Usage: tsx src/db/scripts/make-admin.ts <user_id>");
  process.exit(1);
}

const db = drizzle(neon(url));

async function main() {
  const [updated] = await db
    .update(user)
    .set({ role: "admin" })
    .where(eq(user.id, userId))
    .returning({ id: user.id, email: user.email, role: user.role });

  if (!updated) {
    console.error(`User ${userId} not found.`);
    process.exit(1);
  }

  console.log(`Promoted ${updated.email} (${updated.id}) to admin.`);
}

main().catch((err) => {
  console.error("Failed:", err);
  process.exit(1);
});
