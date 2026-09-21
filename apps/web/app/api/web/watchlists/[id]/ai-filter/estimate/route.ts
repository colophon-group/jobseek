import { NextResponse } from "next/server";

import { getAiFilterEstimate } from "@/lib/ai-filter/estimate-service";
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
    const estimate = await getAiFilterEstimate({
      ownerId,
      watchlistId,
      signal: request.signal,
    });
    return NextResponse.json(estimate, { headers: AI_FILTER_PRIVATE_HEADERS });
  } catch (error) {
    return aiFilterErrorResponse(error);
  }
}
