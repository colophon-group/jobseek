import { type NextRequest, NextResponse } from "next/server";
import { checkPaywall } from "@/lib/agentic/apiPaywall";

const WEB_API = process.env.NEXT_PUBLIC_SITE_URL
  ? `${process.env.NEXT_PUBLIC_SITE_URL}/api/v1`
  : "http://localhost:3000/api/v1";

export async function GET(req: NextRequest) {
  const paywall = await checkPaywall(req);
  if (!paywall.ok) return paywall.response;

  const { searchParams } = req.nextUrl;
  const upstream = new URL(`${WEB_API}/search`);

  for (const [key, value] of searchParams.entries()) {
    upstream.searchParams.set(key, value);
  }

  const res = await fetch(upstream.toString(), {
    headers: { "Accept": "application/json" },
    next: { revalidate: 0 },
  });

  if (!res.ok) {
    return NextResponse.json({ error: "Upstream error" }, { status: res.status });
  }

  const data = await res.json();
  return NextResponse.json(data);
}
