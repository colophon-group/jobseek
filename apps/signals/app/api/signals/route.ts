import { NextResponse } from "next/server";
import { db } from "@/db";
import { hiringSignal, company } from "@/db/schema";
import { desc, eq } from "drizzle-orm";

export async function GET() {
  const signals = await db
    .select({
      id: hiringSignal.id,
      signalType: hiringSignal.signalType,
      signalText: hiringSignal.signalText,
      signalDate: hiringSignal.signalDate,
      score: hiringSignal.score,
      reasoning: hiringSignal.reasoning,
      metadata: hiringSignal.metadata,
      companyName: company.name,
      companySlug: company.slug,
    })
    .from(hiringSignal)
    .leftJoin(company, eq(hiringSignal.companyId, company.id))
    .orderBy(desc(hiringSignal.signalDate))
    .limit(200);

  return NextResponse.json(signals);
}
