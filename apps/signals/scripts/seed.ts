/**
 * Seed script — inserts realistic EU blockchain/AI funding signals directly into the DB.
 * Based on actual signals found by the local RSS test (2026-03-17).
 *
 * Run: DATABASE_URL=... pnpm tsx scripts/seed.ts
 */
import "dotenv/config";
import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
import { eq } from "drizzle-orm";
import { randomUUID } from "crypto";
import * as schema from "../src/db/schema";

const { company, hiringSignal, outreachDraft } = schema;

const DB_URL = process.env.DATABASE_URL;
if (!DB_URL) {
  console.error("DATABASE_URL is not set");
  process.exit(1);
}

const sql = postgres(DB_URL);
const db = drizzle(sql, { schema });

function slugify(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80);
}

interface SeedSignal {
  company: string;
  website: string;
  description: string;
  signalType: string;
  signalText: string;
  signalDate: Date;
  score: number;
  reasoning: string;
  sourceUrl: string;
  careersUrl: string;
  contact: { name: string; title: string; email: string };
  emailSubject: string;
  emailBody: string;
}

const SEEDS: SeedSignal[] = [
  {
    company: "Upvest",
    website: "upvest.co",
    description: "EU investment API platform enabling banks and fintechs to offer securities investing",
    signalType: "funding",
    signalText: "Upvest raised $125M backed by Tencent and Sapphire Ventures to strengthen its API-based investment platform",
    signalDate: new Date("2026-03-10"),
    score: 8.5,
    reasoning: "Large EU fintech raise with strong institutional backers signals aggressive engineering hiring. API-first platform typically needs backend/infra engineers.",
    sourceUrl: "https://sifted.eu/articles/upvest-125m-tencent-sapphire",
    careersUrl: "https://upvest.co/careers",
    contact: { name: "Upvest Hiring Team", title: "VP Engineering", email: "hiring@upvest.co" },
    emailSubject: "Congrats on the $125M round — quick question",
    emailBody: "Hi,\n\nCongratulations on the $125M raise backed by Tencent and Sapphire Ventures — impressive momentum for Upvest's API-first investment infrastructure.\n\nI've been following your platform closely and my background in fintech infrastructure aligns well with where you're likely building next. Would you be open to a 20-minute call to explore whether there's a fit?\n\nBest regards",
  },
  {
    company: "Ironlight",
    website: "ironlight.com",
    description: "Regulated marketplace for tokenized securities — blockchain meets TradFi",
    signalType: "funding",
    signalText: "Ironlight raised $21M to expand its regulated marketplace for tokenized securities",
    signalDate: new Date("2026-03-12"),
    score: 9.2,
    reasoning: "Blockchain + regulated finance is a rare combination. $21M round for a tokenized securities platform directly implies blockchain engineers, compliance tech, and smart contract developers.",
    sourceUrl: "https://www.theblock.co/post/ironlight-raises-21-million-tokenized-securities",
    careersUrl: "https://www.ironlight.com/careers",
    contact: { name: "Ironlight Hiring Team", title: "CTO", email: "hello@ironlight.com" },
    emailSubject: "Congrats on the $21M — smart contract/blockchain angle",
    emailBody: "Hi,\n\nGreat to see Ironlight closing $21M for regulated tokenized securities — this space is moving fast and you're building the right infrastructure layer.\n\nI specialize in blockchain/DeFi infrastructure and have shipped production smart contracts and compliance-aware settlement systems. Would love to explore if there's a fit as you scale the engineering team.\n\nBest",
  },
  {
    company: "Waiv",
    website: "waiv.com",
    description: "Owkin spinout building AI-powered medical diagnostics and testing",
    signalType: "funding",
    signalText: "Waiv (Owkin spinout) raised $33M to scale AI-powered medical testing across Europe",
    signalDate: new Date("2026-03-08"),
    score: 7.8,
    reasoning: "AI + healthcare EU company with significant raise. Owkin pedigree means rigorous ML engineering culture. Likely hiring ML engineers and data platform engineers.",
    sourceUrl: "https://sifted.eu/articles/waiv-owkin-33m-ai-medical-testing",
    careersUrl: "https://waiv.com/careers",
    contact: { name: "Waiv Hiring Team", title: "VP Engineering", email: "careers@waiv.com" },
    emailSubject: "Congrats on the $33M raise — AI/ML infrastructure",
    emailBody: "Hi,\n\nCongratulations on Waiv's $33M raise — scaling AI-powered medical testing is exactly the kind of high-stakes ML application I find most compelling.\n\nWith Owkin's pedigree behind you, I imagine you're building rigorous ML infrastructure. My background in production ML pipelines and healthcare data platforms could be a strong fit. Open to a quick call?\n\nBest",
  },
  {
    company: "Blify",
    website: "blify.io",
    description: "EU AI training platform — pre-seed stage",
    signalType: "funding",
    signalText: "Blify secured $2.1M pre-seed to develop its AI training platform",
    signalDate: new Date("2026-03-14"),
    score: 6.5,
    reasoning: "Early-stage EU AI company at pre-seed. Small team means high leverage per hire. AI training platforms need ML infra engineers. Good fit for someone wanting to join early.",
    sourceUrl: "https://tech.eu/2026/03/14/blify-pre-seed-ai-training",
    careersUrl: "https://blify.io/careers",
    contact: { name: "Blify Founding Team", title: "CTO", email: "team@blify.io" },
    emailSubject: "Congrats on the pre-seed — AI training infra",
    emailBody: "Hi,\n\nCongrats on the $2.1M pre-seed for Blify! Building AI training infrastructure is one of the most technically interesting problems right now.\n\nI'd love to chat about potentially joining early — my background in distributed training systems and MLOps could be useful as you build the core platform. 20 minutes this week?\n\nBest",
  },
  {
    company: "WeSort.AI",
    website: "wesort.ai",
    description: "German AI startup applying ML to critical raw materials supply chain",
    signalType: "funding",
    signalText: "WeSort.AI raised $10M to expand its AI solution for critical raw material sorting and supply chains in Germany",
    signalDate: new Date("2026-03-11"),
    score: 7.1,
    reasoning: "German AI company in the critical materials space — EU strategic priority sector. $10M seed indicates fast growth and hiring. AI + industrial is a unique combination.",
    sourceUrl: "https://eu-startups.com/2026/03/wesort-ai-raises-10m",
    careersUrl: "https://wesort.ai/careers",
    contact: { name: "WeSort.AI Hiring Team", title: "Head of Engineering", email: "jobs@wesort.ai" },
    emailSubject: "Congrats on the $10M — AI for critical materials",
    emailBody: "Hi,\n\nImpressive to see WeSort.AI raise $10M — applying AI to critical raw material supply chains is both technically challenging and strategically important for Europe.\n\nI have a background in computer vision and industrial ML systems and would love to understand where you're building next. Open to a quick call?\n\nBest",
  },
  {
    company: "Nscale",
    website: "nscale.com",
    description: "European GPU cloud and AI infrastructure provider",
    signalType: "funding",
    signalText: "Nscale raised $2B to expand European GPU cloud infrastructure for AI workloads",
    signalDate: new Date("2026-03-13"),
    score: 9.5,
    reasoning: "Massive $2B raise for EU AI infrastructure is extraordinary. Will need hundreds of engineers across distributed systems, networking, and ML infrastructure. Highest priority signal.",
    sourceUrl: "https://tech.eu/2026/03/13/nscale-2b-gpu-cloud",
    careersUrl: "https://nscale.com/careers",
    contact: { name: "Nscale Hiring Team", title: "VP Engineering", email: "careers@nscale.com" },
    emailSubject: "Congrats on the $2B raise — AI infrastructure at scale",
    emailBody: "Hi,\n\nThe $2B raise for Nscale is remarkable — building European AI cloud infrastructure at this scale is a generational opportunity.\n\nI specialize in distributed infrastructure and GPU cluster management, and would be very interested in contributing to what you're building. Could we find 20 minutes to talk?\n\nBest",
  },
  {
    company: "Tracebit",
    website: "tracebit.com",
    description: "Cloud-native deception technology for cybersecurity",
    signalType: "funding",
    signalText: "Tracebit raised $20M Series A to expand its cloud-native deception tech platform",
    signalDate: new Date("2026-03-09"),
    score: 6.8,
    reasoning: "Series A for a cloud security company typically unlocks 10-20 engineering hires. Deception tech is a niche that requires deep security and distributed systems expertise.",
    sourceUrl: "https://tech.eu/2026/03/09/tracebit-20m-series-a",
    careersUrl: "https://tracebit.com/careers",
    contact: { name: "Tracebit Hiring Team", title: "VP Engineering", email: "hello@tracebit.com" },
    emailSubject: "Congrats on the Series A — cloud security engineering",
    emailBody: "Hi,\n\nCongrats on Tracebit's $20M Series A! Cloud-native deception tech is a fascinating security category and the timing feels right.\n\nI have a background in cloud security and distributed systems and would love to explore if there's a fit on the engineering side. Quick call this week?\n\nBest",
  },
];

async function resolveCompanyId(name: string, website: string, description: string): Promise<string> {
  const slug = slugify(name);
  const existing = await db.select({ id: company.id }).from(company).where(eq(company.slug, slug)).limit(1);
  if (existing.length) return existing[0].id;

  const newId = randomUUID();
  await db.insert(company).values({
    id: newId,
    name,
    slug,
    website: `https://${website}`,
    description,
    extras: { synced_from_seed: true },
  }).onConflictDoNothing();

  const refetch = await db.select({ id: company.id }).from(company).where(eq(company.slug, slug)).limit(1);
  return refetch[0]?.id ?? newId;
}

async function seedRecord(seed: SeedSignal) {
  const companyId = await resolveCompanyId(seed.company, seed.website, seed.description);
  const sourceId = `seed-${slugify(seed.company)}-${seed.signalDate.toISOString().split("T")[0]}`;

  // Upsert signal
  const existing = await db.select({ id: hiringSignal.id }).from(hiringSignal).where(eq(hiringSignal.sourceId, sourceId)).limit(1);
  let signalId: string;

  if (existing.length) {
    signalId = existing[0].id;
    await db.update(hiringSignal).set({
      score: seed.score,
      reasoning: seed.reasoning,
      metadata: { source_url: seed.sourceUrl, careers_url: seed.careersUrl, seeded: true },
      updatedAt: new Date(),
    }).where(eq(hiringSignal.id, signalId));
    console.log(`  Updated signal for ${seed.company}`);
  } else {
    signalId = randomUUID();
    await db.insert(hiringSignal).values({
      id: signalId,
      companyId,
      signalType: seed.signalType,
      signalText: seed.signalText,
      signalDate: seed.signalDate,
      sourceId,
      score: seed.score,
      reasoning: seed.reasoning,
      metadata: { source_url: seed.sourceUrl, careers_url: seed.careersUrl, seeded: true },
    });
    console.log(`  Inserted signal for ${seed.company} (score: ${seed.score})`);
  }

  // Upsert outreach draft
  const existingDraft = await db.select({ id: outreachDraft.id }).from(outreachDraft).where(eq(outreachDraft.signalId, signalId)).limit(1);
  if (!existingDraft.length) {
    await db.insert(outreachDraft).values({
      id: randomUUID(),
      signalId,
      contactName: seed.contact.name,
      contactTitle: seed.contact.title,
      contactEmail: seed.contact.email,
      subject: seed.emailSubject,
      body: seed.emailBody,
      status: "pending_review",
    });
    console.log(`  Inserted outreach draft for ${seed.company}`);
  }
}

async function main() {
  console.log(`Seeding ${SEEDS.length} EU blockchain/AI signals…\n`);
  for (const seed of SEEDS) {
    console.log(`→ ${seed.company}`);
    await seedRecord(seed);
  }
  console.log("\nDone.");
  await sql.end();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
