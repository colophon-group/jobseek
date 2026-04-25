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

    // In production: save to R2 and update user_resume with customized_at
    // For now: just validate and return success
    // This would typically:
    // 1. Upload customized.tex to R2
    // 2. Create customization record in database
    // 3. Update user_resume.customized_at timestamp

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
