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

// Under cacheComponents, AuthShell prerenders while request-aware auth
// subtrees stream. `RedirectIfSignedIn` reads the session; AuthForm reads a
// validated `next` search param so save/sign-in flows can return to their
// original context. Both need explicit, stable Suspense boundaries.
export default function AuthLayout({ params, children }: Props) {
  return (
    <AuthShell>
      <Suspense fallback={null}>
        <RedirectIfSignedIn params={params} />
      </Suspense>
      <Suspense fallback={null}>{children}</Suspense>
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
