"use client";

import { X } from "lucide-react";
import { CompanyIcon } from "@/components/CompanyIcon";

export function CompanyPill({
  company,
  onRemove,
}: {
  company: { id: string; name: string; slug: string; icon: string | null };
  onRemove?: (id: string) => void;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border-soft px-2.5 py-1 text-sm">
      <CompanyIcon icon={company.icon} alt={company.name} size={16} />
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
