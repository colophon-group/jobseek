"use server";

import { eq } from "drizzle-orm";
import { db } from "@/db";
import { userResume } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import { extractKeywords } from "@/lib/resume/extract-keywords";

export async function uploadResume(params: {
  filename: string;
  content: string;
}): Promise<{ uploaded: boolean; filename: string }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const { filename, content } = params;

  // Extract keywords from file content
  const keywords = await extractKeywords(content);

  // Upsert resume record
  const [result] = await db
    .insert(userResume)
    .values({
      userId,
      filename,
      keywords,
    })
    .onConflictDoUpdate({
      target: userResume.userId,
      set: {
        filename,
        keywords,
      },
    })
    .returning({ filename: userResume.filename });

  return { uploaded: true, filename: result.filename };
}

export async function getResume(): Promise<{
  filename: string;
  keywords: string[];
} | null> {
  const userId = await getSessionUserId();
  if (!userId) return null;

  const [resume] = await db
    .select({
      filename: userResume.filename,
      keywords: userResume.keywords,
    })
    .from(userResume)
    .where(eq(userResume.userId, userId))
    .limit(1);

  return resume || null;
}

export async function deleteResume(): Promise<void> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  await db.delete(userResume).where(eq(userResume.userId, userId));
}
