import { Suspense } from "react";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { auth } from "@/lib/auth";
import { AuthShell } from "@/components/AuthShell";

type Props = {
  params: Promise<{ lang: string }>;
  children: ReactNode;
};

// Under cacheComponents, the static AuthShell + form prerender.
// `RedirectIfSignedIn` is the only dynamic piece — it streams a session
// lookup and redirects if the visitor is already signed in. Returns null
// otherwise, so there's no visual placeholder to flash.
export default function AuthLayout({ params, children }: Props) {
  return (
    <AuthShell>
      <Suspense fallback={null}>
        <RedirectIfSignedIn params={params} />
      </Suspense>
      {children}
    </AuthShell>
  );
}

async function RedirectIfSignedIn({ params }: { params: Promise<{ lang: string }> }) {
  const [{ lang }, session] = await Promise.all([
    params,
    auth.api.getSession({ headers: await headers() }),
  ]);
  if (session) redirect(`/${lang}/explore`);
  return null;
}
