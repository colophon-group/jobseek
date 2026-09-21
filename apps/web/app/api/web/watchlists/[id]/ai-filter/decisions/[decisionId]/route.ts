import { NextResponse } from "next/server";

import {
  moveAiFilterDecision,
  reportAiFilterMistake,
  undoAiFilterDecisionMove,
} from "@/lib/ai-filter/decision-service";
import {
  AI_FILTER_PRIVATE_HEADERS,
  aiFilterErrorResponse,
  aiFilterNotFoundResponse,
  isUuid,
  readSmallJson,
} from "@/lib/ai-filter/route-utils";
import { getSessionUserIdFromHeaders } from "@/lib/sessionCache";

type Context = { params: Promise<{ id: string; decisionId: string }> };

async function ownerAndIds(request: Request, context: Context) {
  const [ownerId, params] = await Promise.all([
    getSessionUserIdFromHeaders(request.headers),
    context.params,
  ]);
  return ownerId && isUuid(params.id) && isUuid(params.decisionId)
    ? { ownerId, watchlistId: params.id, decisionId: params.decisionId }
    : null;
}

export async function PATCH(request: Request, context: Context) {
  const owner = await ownerAndIds(request, context);
  if (!owner) return aiFilterNotFoundResponse();
  try {
    const body = await readSmallJson(request);
    if (
      Reflect.ownKeys(body).length !== 2 ||
      (body.decision !== "accepted" && body.decision !== "rejected")
    ) {
      throw new TypeError("Invalid decision move");
    }
    const result = await moveAiFilterDecision({
      ...owner,
      to: body.decision,
      idempotencyKey: body.idempotencyKey,
    });
    return NextResponse.json(result, { headers: AI_FILTER_PRIVATE_HEADERS });
  } catch (error) {
    return aiFilterErrorResponse(error);
  }
}

export async function DELETE(request: Request, context: Context) {
  const owner = await ownerAndIds(request, context);
  if (!owner) return aiFilterNotFoundResponse();
  try {
    const body = await readSmallJson(request);
    if (Reflect.ownKeys(body).length !== 2) {
      throw new TypeError("Invalid decision undo");
    }
    const result = await undoAiFilterDecisionMove({
      ...owner,
      moveIdempotencyKey: body.moveIdempotencyKey,
      idempotencyKey: body.idempotencyKey,
    });
    return NextResponse.json(result, { headers: AI_FILTER_PRIVATE_HEADERS });
  } catch (error) {
    return aiFilterErrorResponse(error);
  }
}

export async function POST(request: Request, context: Context) {
  const owner = await ownerAndIds(request, context);
  if (!owner) return aiFilterNotFoundResponse();
  try {
    const body = await readSmallJson(request);
    if (Reflect.ownKeys(body).length !== 1) {
      throw new TypeError("Invalid mistake report");
    }
    const result = await reportAiFilterMistake({
      ...owner,
      idempotencyKey: body.idempotencyKey,
    });
    return NextResponse.json(result, { headers: AI_FILTER_PRIVATE_HEADERS });
  } catch (error) {
    return aiFilterErrorResponse(error);
  }
}
