import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";

export function BackLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link
      href={href}
      prefetch={false}
      className="inline-flex items-center gap-1.5 text-xs text-muted transition-colors hover:text-foreground"
    >
      <ArrowLeft size={13} />
      {children}
    </Link>
  );
}
