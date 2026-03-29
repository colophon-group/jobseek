import { NextRequest, NextResponse } from "next/server";
import { db } from "@/db";
import { outreachDraft } from "@/db/schema";
import { eq } from "drizzle-orm";

const VALID_STATUSES = ["pending_review", "sent", "archived"] as const;
type Status = (typeof VALID_STATUSES)[number];

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const body = await req.json().catch(() => ({}));
  const { subject, body: draftBody, status } = body as {
    subject?: string;
    body?: string;
    status?: string;
  };

  if (status && !VALID_STATUSES.includes(status as Status)) {
    return NextResponse.json({ error: "Invalid status" }, { status: 400 });
  }

  const updates: Partial<typeof outreachDraft.$inferInsert> = {
    updatedAt: new Date(),
  };
  if (subject !== undefined) updates.subject = subject;
  if (draftBody !== undefined) updates.body = draftBody;
  if (status !== undefined) updates.status = status as Status;

  const rows = await db
    .update(outreachDraft)
    .set(updates)
    .where(eq(outreachDraft.id, id))
    .returning();

  if (!rows.length) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  return NextResponse.json(rows[0]);
}
