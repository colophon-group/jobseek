"use client";

import { ThemeToggleButton } from "@/components/ThemeToggleButton";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { ThemedImage } from "@/components/ThemedImage";
import type { ReactNode } from "react";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto max-w-sm px-4">
      <div className="flex min-h-screen flex-col items-center justify-center py-8">
        <div className="mb-6 h-9 w-36">
          <ThemedImage
            lightSrc="/js_wide_logo_black.svg"
            darkSrc="/js_wide_logo_white.svg"
            alt="Job Seek"
            width={144}
            height={36}
          />
        </div>
        <div className="w-full rounded-lg border border-border-soft bg-surface p-6 sm:p-8">
          {children}
        </div>
        <div className="mt-4 flex gap-1">
          <ThemeToggleButton />
          <LocaleSwitcher />
        </div>
      </div>
    </div>
  );
}
