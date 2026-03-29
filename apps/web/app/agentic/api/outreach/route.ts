import { NextResponse } from "next/server";
import { db } from "@/db";
import { outreachDraft } from "@/db/schema";
import { desc } from "drizzle-orm";

export async function GET() {
  const drafts = await db
    .select({
      id: outreachDraft.id,
      signalId: outreachDraft.signalId,
      contactName: outreachDraft.contactName,
      contactTitle: outreachDraft.contactTitle,
      contactEmail: outreachDraft.contactEmail,
      subject: outreachDraft.subject,
      body: outreachDraft.body,
      status: outreachDraft.status,
      createdAt: outreachDraft.createdAt,
    })
    .from(outreachDraft)
    .orderBy(desc(outreachDraft.createdAt))
    .limit(100);

  return NextResponse.json(drafts);
}
