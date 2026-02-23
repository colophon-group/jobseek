import { headers } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { auth } from "@/lib/auth";

type Props = {
  params: Promise<{ lang: string }>;
  children: ReactNode;
};

export default async function DashboardLayout({ params, children }: Props) {
  const { lang } = await params;
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session) redirect(`/${lang}/sign-in`);

  return <>{children}</>;
}
