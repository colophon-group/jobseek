import { NextResponse } from "next/server";

import {
  listAiFilterDecisions,
  listSharedAiFilterDecisions,
} from "@/lib/ai-filter/decision-service";
import {
  AiFilterNotFoundError,
  getAiFilterOwnerState,
} from "@/lib/ai-filter/configuration-service";
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
  const [viewerId, { id: watchlistId }] = await Promise.all([
    getSessionUserIdFromHeaders(request.headers),
    context.params,
  ]);
  if (!isUuid(watchlistId)) return aiFilterNotFoundResponse();
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
    const pagination = {
      watchlistId,
      offset: Number(rawOffset),
      limit: Number(rawLimit),
      signal: request.signal,
    };
    let payload;
    if (viewerId) {
      try {
        const owner = { ownerId: viewerId, watchlistId };
        const [page, state] = await Promise.all([
          listAiFilterDecisions({
            ...pagination,
            ownerId: viewerId,
            bucket,
          }),
          getAiFilterOwnerState(owner),
        ]);
        payload = { ...page, state };
      } catch (error) {
        if (bucket !== "accepted" || !(error instanceof AiFilterNotFoundError)) {
          throw error;
        }
        payload = await listSharedAiFilterDecisions(pagination);
      }
    } else {
      if (bucket !== "accepted") return aiFilterNotFoundResponse();
      payload = await listSharedAiFilterDecisions(pagination);
    }
    return NextResponse.json(payload, { headers: AI_FILTER_PRIVATE_HEADERS });
  } catch (error) {
    return aiFilterErrorResponse(error);
  }
}
