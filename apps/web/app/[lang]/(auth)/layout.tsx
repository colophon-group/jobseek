import { Suspense } from "react";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { auth } from "@/lib/auth";
import { shouldRedirectSignedInUser } from "@/lib/auth-page-session";
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
  const requestHeaders = await headers();
  const [{ lang }, session] = await Promise.all([
    params,
    auth.api.getSession({ headers: requestHeaders }),
  ]);
  // The client bootstrap intentionally treats an absent `logged_in` hint as
  // anonymous. Do not redirect a real legacy session away from the repair
  // surface unless that hint is present too; submitting the form recreates it.
  if (
    shouldRedirectSignedInUser(
      Boolean(session),
      requestHeaders.get("cookie") ?? "",
    )
  ) {
    redirect(`/${lang}/explore`);
  }
  return null;
}
