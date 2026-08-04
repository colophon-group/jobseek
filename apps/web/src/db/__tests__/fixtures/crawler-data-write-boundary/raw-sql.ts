import { db } from "@/db";
import { sql } from "drizzle-orm";

void db.execute(sql`UPDATE "public"."job_posting" SET is_active = false`);
