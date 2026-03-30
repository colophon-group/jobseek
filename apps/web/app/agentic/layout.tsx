"use client";

import Link from "next/link";
import { Bot } from "lucide-react";
import ThemeToggle from "@/components/agentic/ThemeToggle";

const iconBtn =
  "inline-flex items-center justify-center rounded-md p-1.5 text-foreground hover:bg-border-soft transition-colors cursor-pointer";

export default function AgenticLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-col min-h-screen bg-background">
      <header className="sticky top-0 z-50 border-b border-divider bg-surface-alpha backdrop-blur-md">
        <div className="mx-auto flex h-12 max-w-[1200px] items-center gap-4 px-4">
          <Link href="/agentic" className="inline-flex shrink-0 items-center gap-2 text-foreground no-underline">
            <div className="size-6 rounded-md bg-primary" />
            <span className="text-sm font-semibold tracking-tight">Agentic API</span>
          </Link>

          <div className="flex-1" />

          <div className="flex items-center gap-3">
            <Link
              href="/en"
              className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-foreground transition-colors"
            >
              ← Job Seek
            </Link>
            <ThemeToggle />
            <Link href="/agentic" className={iconBtn} aria-label="Agentic">
              <Bot size={18} strokeWidth={1.8} aria-hidden="true" />
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-8">
        {children}
      </main>
    </div>
  );
}
