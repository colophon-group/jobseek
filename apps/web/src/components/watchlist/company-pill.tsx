"use client";

import { X } from "lucide-react";
import Link from "next/link";
import { useLingui } from "@lingui/react/macro";
import { CompanyIcon } from "@/components/CompanyIcon";
import { useLocalePath } from "@/lib/useLocalePath";

export function CompanyPill({
  company,
  onRemove,
}: {
  company: { id: string; name: string; slug: string; icon: string | null; unavailable?: boolean };
  onRemove?: (id: string) => void;
}) {
  const { t } = useLingui();
  const lp = useLocalePath();
  const name = company.unavailable
    ? t({ id: "companies.selection.unavailableLabel", comment: "Label for a retained company selection whose display data is unavailable", message: "Company unavailable" })
    : company.name;
  return (
    <span className="inline-flex items-center rounded-full border border-border-soft text-sm">
      {company.unavailable ? (
        <span className="inline-flex min-h-7 items-center gap-1.5 px-2.5 py-1 text-muted">
          <span className="max-w-[120px] truncate">{name}</span>
        </span>
      ) : <Link
        href={lp(`/company/${company.slug}`)}
        prefetch={false}
        className={`inline-flex min-h-7 cursor-pointer items-center gap-1.5 rounded-full px-2.5 py-1 transition-colors hover:bg-border-soft active:bg-primary/10 active:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-surface ${onRemove ? "pr-1.5" : ""}`}
      >
        <CompanyIcon icon={company.icon} alt="" size={16} />
        <span className="max-w-[120px] truncate">{name}</span>
      </Link>}
      {onRemove && (
        <button
          type="button"
          onClick={() => onRemove(company.id)}
          className="mr-0.5 inline-flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted transition-colors hover:bg-border-soft hover:text-foreground active:bg-primary/10 active:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
          aria-label={t({ id: "watchlists.companyPill.remove", comment: "Aria label for the X button that removes a company pill; {name} is the company name", message: `Remove ${name}` })}
        >
          <X size={12} aria-hidden="true" />
        </button>
      )}
    </span>
  );
}
