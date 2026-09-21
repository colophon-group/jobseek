import { NextResponse } from "next/server";

import { AiFilterContractError } from "./contract";
import { AiFilterCandidateLoadError } from "./candidate-loader";
import {
  AiFilterEntitlementError,
  AiFilterNotFoundError,
} from "./configuration-service";
import { AiFilterAuthorizationError } from "./postgres-repository";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export const AI_FILTER_PRIVATE_HEADERS = { "Cache-Control": "private, no-store" };

export function isUuid(value: string): boolean {
  return UUID_PATTERN.test(value);
}

export function aiFilterNotFoundResponse() {
  return NextResponse.json(
    { error: "not_found" },
    { status: 404, headers: AI_FILTER_PRIVATE_HEADERS },
  );
}

export function aiFilterErrorResponse(error: unknown) {
  if (
    error instanceof AiFilterNotFoundError ||
    error instanceof AiFilterAuthorizationError ||
    (error instanceof AiFilterCandidateLoadError && error.code === "not_found")
  ) {
    return aiFilterNotFoundResponse();
  }
  if (error instanceof AiFilterEntitlementError) {
    return NextResponse.json(
      { error: "subscription_required" },
      { status: 403, headers: AI_FILTER_PRIVATE_HEADERS },
    );
  }
  if (
    error instanceof AiFilterContractError ||
    error instanceof TypeError ||
    error instanceof SyntaxError
  ) {
    return NextResponse.json(
      { error: "invalid_request" },
      { status: 400, headers: AI_FILTER_PRIVATE_HEADERS },
    );
  }
  if (error instanceof AiFilterCandidateLoadError) {
    return NextResponse.json(
      { error: "temporarily_unavailable" },
      {
        status: 503,
        headers: { ...AI_FILTER_PRIVATE_HEADERS, "Retry-After": "30" },
      },
    );
  }
  return NextResponse.json(
    { error: "temporarily_unavailable" },
    {
      status: 503,
      headers: { ...AI_FILTER_PRIVATE_HEADERS, "Retry-After": "30" },
    },
  );
}

export async function readSmallJson(request: Request): Promise<Record<string, unknown>> {
  const declaredLength = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declaredLength) && declaredLength > 4_096) {
    throw new TypeError("AI filter request is too large");
  }
  const text = await request.text();
  if (text.length > 4_096) throw new TypeError("AI filter request is too large");
  const value: unknown = JSON.parse(text);
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("AI filter request must be an object");
  }
  return value as Record<string, unknown>;
}
