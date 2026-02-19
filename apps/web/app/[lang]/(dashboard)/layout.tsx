import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { stackServerApp } from "@/stack/server";

type Props = {
  children: ReactNode;
};

export default async function DashboardLayout({ children }: Props) {
  const user = await stackServerApp.getUser();
  if (!user) redirect("/handler/signup");

  return <>{children}</>;
}
