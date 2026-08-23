"use client";

import Link from "next/link";
import { ThemeToggleButton } from "@/components/ThemeToggleButton";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { SkipToContentLink } from "@/components/SkipToContentLink";
import { ThemedImage } from "@/components/ThemedImage";
import { useLocalePath } from "@/lib/useLocalePath";
import type { ReactNode } from "react";

export function AuthShell({ children }: { children: ReactNode }) {
  const lp = useLocalePath();

  return (
    <>
      <SkipToContentLink />
      <main
        id="main-content"
        tabIndex={-1}
        className="mx-auto w-full max-w-lg px-4 scroll-mt-12 sm:w-fit sm:min-w-[24rem]"
        suppressHydrationWarning
      >
        <div className="flex min-h-screen flex-col items-center justify-center py-8">
          <Link href={lp("/explore")} prefetch={false} className="mb-6 block h-9 w-36">
            <ThemedImage
              lightSrc="/js_wide_logo_black.svg"
              darkSrc="/js_wide_logo_white.svg"
              alt="Job Seek"
              width={144}
              height={36}
              loading="eager"
              fetchPriority="high"
            />
          </Link>
          <div className="w-full rounded-lg border border-border-soft bg-surface p-6 sm:p-8">
            {children}
          </div>
          <div className="mt-4 flex gap-1">
            <ThemeToggleButton />
            <LocaleSwitcher />
          </div>
        </div>
      </main>
    </>
  );
}
