import { NextResponse } from "next/server";
import { headers } from "next/headers";
import { cookies } from "next/headers";
import { eq } from "drizzle-orm";
import * as OTPAuth from "otpauth";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { twoFactor } from "@/db/schema";
import { signAdminCookie } from "@/lib/admin-cookie";

export async function POST(request: Request) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await request.json();
  const code = body.code as string;

  if (!code || !/^\d{6}$/.test(code)) {
    return NextResponse.json({ error: "Invalid code format" }, { status: 400 });
  }

  // Read TOTP secret from DB
  const [record] = await db
    .select()
    .from(twoFactor)
    .where(eq(twoFactor.userId, session.user.id));

  if (!record) {
    return NextResponse.json({ error: "2FA not configured" }, { status: 400 });
  }

  // Verify TOTP code
  const totp = new OTPAuth.TOTP({
    secret: OTPAuth.Secret.fromBase32(record.secret),
    algorithm: "SHA1",
    digits: 6,
    period: 30,
  });

  const delta = totp.validate({ token: code, window: 1 });
  if (delta === null) {
    return NextResponse.json({ error: "Invalid code" }, { status: 400 });
  }

  // Set signed cookie
  const cookieStore = await cookies();
  cookieStore.set("admin_2fa_verified", signAdminCookie(session.user.id), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 8 * 60 * 60, // 8 hours
  });

  return NextResponse.json({ ok: true });
}
