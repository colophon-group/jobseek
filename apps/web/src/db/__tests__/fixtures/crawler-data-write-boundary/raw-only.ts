import { db } from "@/db";
import { sql } from "drizzle-orm";

void db.execute(sql`SELECT id FROM ONLY public.job_posting`);
