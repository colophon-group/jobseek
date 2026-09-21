import { NextResponse } from "next/server";

import { listAiFilterDecisions } from "@/lib/ai-filter/decision-service";
import {
  AI_FILTER_PRIVATE_HEADERS,
  aiFilterErrorResponse,
  aiFilterNotFoundResponse,
  isUuid,
} from "@/lib/ai-filter/route-utils";
import { getSessionUserIdFromHeaders } from "@/lib/sessionCache";

export async function GET(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const [ownerId, { id: watchlistId }] = await Promise.all([
    getSessionUserIdFromHeaders(request.headers),
    context.params,
  ]);
  if (!ownerId || !isUuid(watchlistId)) return aiFilterNotFoundResponse();
  try {
    const params = new URL(request.url).searchParams;
    const bucket = params.get("bucket");
    if (bucket !== "accepted" && bucket !== "rejected") {
      throw new TypeError("Invalid decision bucket");
    }
    const rawOffset = params.get("offset") ?? "0";
    const rawLimit = params.get("limit") ?? "25";
    if (!/^\d{1,9}$/.test(rawOffset) || !/^\d{1,3}$/.test(rawLimit)) {
      throw new TypeError("Invalid pagination");
    }
    const page = await listAiFilterDecisions({
      ownerId,
      watchlistId,
      bucket,
      offset: Number(rawOffset),
      limit: Number(rawLimit),
      signal: request.signal,
    });
    return NextResponse.json(page, { headers: AI_FILTER_PRIVATE_HEADERS });
  } catch (error) {
    return aiFilterErrorResponse(error);
  }
}
