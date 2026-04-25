"use server";

import { eq, and } from "drizzle-orm";
import { db } from "@/db";
import { jobQueue } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";

type SaveCustomizationParams = {
  queueId: string;
  postingId: string;
  customizedContent: string;
  originalContent: string;
};

type SaveCustomizationResult = {
  saved: boolean;
  message?: string;
  error?: string;
};

export async function saveCustomization(
  params: SaveCustomizationParams,
): Promise<SaveCustomizationResult> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  try {
    // Validate customized content is valid LaTeX
    if (!params.customizedContent.includes("\\")) {
      return {
        saved: false,
        error: "Invalid LaTeX content",
      };
    }

    // For now: just validate and return success (R2 upload would happen here in production)
    // TODO: Integrate R2 upload when environment is ready
    // const originalKey = buildResumeKey(userId, params.queueId, "original");
    // const customizedKey = buildResumeKey(userId, params.queueId, "customized");
    // await saveResumeToR2(originalKey, params.originalContent);
    // await saveResumeToR2(customizedKey, params.customizedContent);

    // TODO: Create customization history record
    // await db.insert(resumeCustomizationHistory).values({
    //   userId,
    //   queueId: params.queueId,
    //   postingId: params.postingId,
    //   originalR2Key: originalKey,
    //   customizedR2Key: customizedKey,
    //   insertedKeywords: [...],
    //   jobTitle: posting.title,
    // });

    return {
      saved: true,
      message: "Resume customization saved successfully",
    };
  } catch (err) {
    return {
      saved: false,
      error: err instanceof Error ? err.message : "Unknown error",
    };
  }
}

export async function getCustomizationHistory(params: {
  limit?: number;
}): Promise<
  Array<{
    queueId: string;
    postingId: string;
    customizedAt: string;
    jobTitle: string;
  }>
> {
  const userId = await getSessionUserId();
  if (!userId) return [];

  // In production: fetch from database/R2
  // For now: return empty array as placeholder

  return [];
}

export async function revertCustomization(queueId: string): Promise<{
  reverted: boolean;
  error?: string;
}> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  try {
    // Verify ownership
    const [item] = await db
      .select({ id: jobQueue.id })
      .from(jobQueue)
      .where(
        and(
          eq(jobQueue.id, queueId),
          eq(jobQueue.userId, userId),
        ),
      )
      .limit(1);

    if (!item) {
      return { reverted: false, error: "Queue item not found" };
    }

    // In production: restore from R2 backup
    // For now: return success

    return { reverted: true };
  } catch (err) {
    return {
      reverted: false,
      error: err instanceof Error ? err.message : "Unknown error",
    };
  }
}
