"use client";

import Image from "next/image";
import { Building2, X } from "lucide-react";

export function CompanyPill({
  company,
  onRemove,
}: {
  company: { id: string; name: string; slug: string; icon: string | null };
  onRemove?: (id: string) => void;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border-soft px-2.5 py-1 text-sm">
      {company.icon ? (
        <Image
          src={company.icon}
          alt={company.name}
          width={16}
          height={16}
          sizes="16px"
          className="size-4 shrink-0 rounded"
        />
      ) : (
        <Building2 size={14} className="shrink-0 text-muted" />
      )}
      <span className="max-w-[120px] truncate">{company.name}</span>
      {onRemove && (
        <button
          type="button"
          onClick={() => onRemove(company.id)}
          className="ml-0.5 rounded-full p-0.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground cursor-pointer"
        >
          <X size={12} />
        </button>
      )}
    </span>
  );
}
