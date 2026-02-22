import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { eq } from "drizzle-orm";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { usersMeta } from "@/db/schema";
import { verifyAdminCookie } from "@/lib/admin-cookie";

type Props = {
  children: ReactNode;
  params: Promise<{ lang: string }>;
};

export default async function AdminLayout({ children, params }: Props) {
  const { lang } = await params;

  // 1. Check authenticated
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session) redirect("/sign-in");

  // 2. Check admin role
  const [meta] = await db
    .select()
    .from(usersMeta)
    .where(eq(usersMeta.userId, session.user.id));

  if (!meta || meta.role !== "admin") {
    redirect(`/${lang}/dashboard`);
  }

  // 3. Check 2FA is enabled on the user
  if (!session.user.twoFactorEnabled) {
    redirect(`/${lang}/dashboard`);
  }

  // 4. Check admin_2fa_verified cookie
  const cookieStore = await cookies();
  const token = cookieStore.get("admin_2fa_verified")?.value;
  if (!token || !verifyAdminCookie(token, session.user.id)) {
    redirect(`/${lang}/admin/verify-2fa`);
  }

  return <>{children}</>;
}
