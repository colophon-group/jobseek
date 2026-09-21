import { NextResponse } from "next/server";

import { getAiFilterOwnerState } from "@/lib/ai-filter/configuration-service";
import {
  AI_FILTER_PRIVATE_HEADERS,
  aiFilterErrorResponse,
  aiFilterNotFoundResponse,
  isUuid,
} from "@/lib/ai-filter/route-utils";
import { startAiFilterCatchup } from "@/lib/ai-filter/workflow-trigger";
import { getSessionUserIdFromHeaders } from "@/lib/sessionCache";

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const [ownerId, { id: watchlistId }] = await Promise.all([
    getSessionUserIdFromHeaders(request.headers),
    context.params,
  ]);
  if (!ownerId || !isUuid(watchlistId)) return aiFilterNotFoundResponse();
  try {
    const owner = { ownerId, watchlistId };
    const state = await getAiFilterOwnerState(owner);
    if (!state.enabled) {
      return NextResponse.json(
        { state, workflow: null },
        { headers: AI_FILTER_PRIVATE_HEADERS },
      );
    }
    const workflow = await startAiFilterCatchup(owner);
    return NextResponse.json(
      { state, workflow },
      { status: 202, headers: AI_FILTER_PRIVATE_HEADERS },
    );
  } catch (error) {
    return aiFilterErrorResponse(error);
  }
}
