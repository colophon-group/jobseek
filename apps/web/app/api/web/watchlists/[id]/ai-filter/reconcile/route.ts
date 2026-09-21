import { NextResponse } from "next/server";

import {
  getAiFilterOwnerState,
  putAiFilterConfiguration,
} from "@/lib/ai-filter/configuration-service";
import {
  aiFilterDemandIsCovered,
  aiFilterDemandTarget,
} from "@/lib/ai-filter/demand";
import {
  AI_FILTER_PRIVATE_HEADERS,
  aiFilterErrorResponse,
  aiFilterNotFoundResponse,
  isUuid,
  readSmallJson,
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
    const body = await readSmallJson(request);
    if (Reflect.ownKeys(body).length !== 1 || !("offset" in body)) {
      throw new TypeError("AI filter demand contains unsupported fields");
    }
    const demandTargetOffset = aiFilterDemandTarget(body.offset);
    let state = await getAiFilterOwnerState(owner);
    if (!state.enabled) {
      return NextResponse.json(
        { state, workflow: null },
        { headers: AI_FILTER_PRIVATE_HEADERS },
      );
    }
    // Reusing the same query is idempotent unless the hard-filter fingerprint
    // changed. In that case this creates a fresh revision at offset zero;
    // unchanged job/query pairs still hit the global semantic cache.
    state = await putAiFilterConfiguration({ ...owner, query: state.query });
    if (aiFilterDemandIsCovered({
      targetOffset: demandTargetOffset,
      selectionOffset: state.progress.selectionOffset,
      scannedCount: state.progress.scannedCount,
      caughtUp: state.status === "caught_up",
    })) {
      return NextResponse.json(
        { state, workflow: null },
        { headers: AI_FILTER_PRIVATE_HEADERS },
      );
    }
    const workflow = await startAiFilterCatchup({
      ...owner,
      demandTargetOffset,
    });
    return NextResponse.json(
      { state, workflow },
      { status: 202, headers: AI_FILTER_PRIVATE_HEADERS },
    );
  } catch (error) {
    return aiFilterErrorResponse(error);
  }
}
