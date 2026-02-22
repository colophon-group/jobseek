import dotenv from "dotenv";
dotenv.config({ path: ".env.local" });
import { neon } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-http";
import { companies, jobPostings, subscriptions } from "./schema";

const url = process.env.DATABASE_URL_UNPOOLED ?? process.env.DATABASE_URL;
if (!url) {
  throw new Error("DATABASE_URL_UNPOOLED or DATABASE_URL must be set");
}

const db = drizzle(neon(url));

async function main() {
  console.log("Seeding database...");

  // --- Companies ---
  const [acme, globex, initech] = await db
    .insert(companies)
    .values([
      {
        name: "Acme Corp",
        slug: "acme-corp",
        jobBoard: "https://jobs.acme.example.com",
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
    .returning();

  // --- Job Postings ---
  await db.insert(jobPostings).values([
    {
      companyId: acme.id,
      title: "Senior Frontend Engineer",
      description: "Build amazing user interfaces with React and Next.js.",
      location: "Berlin, Germany",
    },
    {
      companyId: acme.id,
      title: "Backend Developer",
      description: "Design and implement scalable APIs.",
      location: "Remote",
    },
    {
      companyId: globex.id,
      title: "Product Manager",
      location: "Munich, Germany",
    },
    {
      companyId: globex.id,
      title: "DevOps Engineer",
      description: "Manage CI/CD pipelines and cloud infrastructure.",
      location: "Vienna, Austria",
    },
    {
      companyId: initech.id,
      title: "Full Stack Developer",
      description: "Work across the entire stack with TypeScript.",
      location: "Zurich, Switzerland",
    },
  ]);

  // --- Subscriptions (placeholder user IDs — no real users in seed) ---
  // Skipped: subscriptions reference user.id via FK, so seeding
  // requires real Better Auth users. Create subscriptions after sign-up.

  console.log("Seed complete.");
}

main().catch((err) => {
  console.error("Seed failed:", err);
  process.exit(1);
});
