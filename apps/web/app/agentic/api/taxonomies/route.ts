import { type NextRequest, NextResponse } from "next/server";
import { checkPaywall } from "@/lib/agentic/apiPaywall";

const WEB_API = process.env.NEXT_PUBLIC_SITE_URL
  ? `${process.env.NEXT_PUBLIC_SITE_URL}/api/v1`
  : "http://localhost:3000/api/v1";

const VALID_TYPES = new Set(["seniority", "occupations", "technologies", "industries"]);

export async function GET(req: NextRequest) {
  const paywall = await checkPaywall(req);
  if (!paywall.ok) return paywall.response;

  const type = req.nextUrl.searchParams.get("type");
  if (!type || !VALID_TYPES.has(type)) {
    return NextResponse.json(
      { error: "Missing or invalid 'type'. Must be one of: seniority, occupations, technologies, industries" },
      { status: 400 }
    );
  }

  const locale = req.nextUrl.searchParams.get("locale") ?? "en";
  const upstream = new URL(`${WEB_API}/taxonomies`);
  upstream.searchParams.set("type", type);
  upstream.searchParams.set("locale", locale);

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
