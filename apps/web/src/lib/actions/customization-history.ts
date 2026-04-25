"use server";

import { eq } from "drizzle-orm";
import { db } from "@/db";
import { resumeCustomizationHistory } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";

export type CustomizationHistoryItem = {
  id: string;
  queueId: string;
  postingId: string;
  jobTitle: string;
  insertedKeywords: string[];
  createdAt: Date;
};

export async function getCustomizationHistory(params: {
  limit?: number;
  offset?: number;
} = {}): Promise<CustomizationHistoryItem[]> {
  const userId = await getSessionUserId();
  if (!userId) return [];

  const limit = params.limit || 10;
  const offset = params.offset || 0;

  try {
    const items = await db
      .select({
        id: resumeCustomizationHistory.id,
        queueId: resumeCustomizationHistory.queueId,
        postingId: resumeCustomizationHistory.postingId,
        jobTitle: resumeCustomizationHistory.jobTitle,
        insertedKeywords: resumeCustomizationHistory.insertedKeywords,
        createdAt: resumeCustomizationHistory.createdAt,
      })
      .from(resumeCustomizationHistory)
      .where(eq(resumeCustomizationHistory.userId, userId))
      .orderBy((table) => table.createdAt)
      .limit(limit)
      .offset(offset);

    return items.map((item) => ({
      ...item,
      createdAt: new Date(item.createdAt),
    }));
  } catch {
    return [];
  }
}

export async function getCustomizationCount(): Promise<number> {
  const userId = await getSessionUserId();
  if (!userId) return 0;

  try {
    const result = await db
      .select({ count: resumeCustomizationHistory.id })
      .from(resumeCustomizationHistory)
      .where(eq(resumeCustomizationHistory.userId, userId));

    return result.length;
  } catch {
    return 0;
  }
}

export async function deleteCustomization(
  customizationId: string,
): Promise<{ deleted: boolean; error?: string }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  try {
    const [item] = await db
      .select({ id: resumeCustomizationHistory.id })
      .from(resumeCustomizationHistory)
      .where(eq(resumeCustomizationHistory.id, customizationId))
      .limit(1);

    if (!item) {
      return { deleted: false, error: "Customization not found" };
    }

    // TODO: Delete from R2 if stored there
    // await deleteFromR2(customization.originalR2Key);
    // await deleteFromR2(customization.customizedR2Key);

    return { deleted: true };
  } catch (err) {
    return {
      deleted: false,
      error: err instanceof Error ? err.message : "Unknown error",
    };
  }
}
