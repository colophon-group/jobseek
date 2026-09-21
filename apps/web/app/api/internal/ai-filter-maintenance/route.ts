import { timingSafeEqual } from "node:crypto";
import { NextResponse } from "next/server";

import {
  cleanupAiFilterRetention,
  sweepAiFilterCatchup,
} from "@/lib/ai-filter/maintenance-service";

function safeBearerEqual(presented: string | null, expected: string): boolean {
  if (!presented) return false;
  const actual = Buffer.from(presented);
  const wanted = Buffer.from(`Bearer ${expected}`);
  return actual.length === wanted.length && timingSafeEqual(actual, wanted);
}

export async function GET(request: Request) {
  const secret = process.env.CRON_SECRET;
  if (!secret) {
    return NextResponse.json(
      { error: "temporarily_unavailable" },
      { status: 503, headers: { "Cache-Control": "private, no-store" } },
    );
  }
  if (!safeBearerEqual(request.headers.get("authorization"), secret)) {
    return NextResponse.json(
      { error: "unauthorized" },
      { status: 401, headers: { "Cache-Control": "private, no-store" } },
    );
  }
  const [cleanup, sweep] = await Promise.all([
    cleanupAiFilterRetention(),
    sweepAiFilterCatchup(),
  ]);
  return NextResponse.json(
    { ok: true, cleanup, sweep },
    { headers: { "Cache-Control": "private, no-store" } },
  );
}
