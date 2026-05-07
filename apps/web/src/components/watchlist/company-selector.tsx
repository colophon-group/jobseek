"use client";

import { useState, useRef, useEffect } from "react";
import { Search, Loader2 } from "lucide-react";
import { useLingui } from "@lingui/react/macro";
import { CompanyIcon } from "@/components/CompanyIcon";
import { suggestCompanies, type CompanySuggestion } from "@/lib/actions/company";
import { CompanyPill } from "./company-pill";

type SelectedCompany = { id: string; name: string; slug: string; icon: string | null };

export function CompanySelector({
  selected,
  onChange,
  hidePills,
}: {
  selected: SelectedCompany[];
  onChange: (companies: SelectedCompany[]) => void;
  hidePills?: boolean;
}) {
  const { t } = useLingui();
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<CompanySuggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  useEffect(() => {
    clearTimeout(timerRef.current);
    if (query.length < 2) {
      setSuggestions([]);
      return;
    }
    timerRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const ids = new Set(selectedRef.current.map((c) => c.id));
        const results = await suggestCompanies({ query });
        setSuggestions(results.filter((r) => !ids.has(r.id)));
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => clearTimeout(timerRef.current);
  }, [query]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  function handleSelect(company: CompanySuggestion) {
    onChange([...selected, company]);
    setQuery("");
    setSuggestions([]);
  }

  function handleRemove(id: string) {
    onChange(selected.filter((c) => c.id !== id));
  }

  return (
    <div ref={containerRef} className="space-y-2">
      {!hidePills && selected.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {selected.map((c) => (
            <CompanyPill key={c.id} company={c} onRemove={handleRemove} />
          ))}
        </div>
      )}

      <div className="relative">
        <div className="flex items-center gap-2 rounded-md border border-border-soft px-3 py-2">
          <Search size={14} className="shrink-0 text-muted" />
          <input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            placeholder={t({
              id: "watchlists.companySelector.placeholder",
              comment: "Placeholder for company search in watchlist creation",
              message: "Search companies...",
            })}
            className="w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted"
          />
          {loading && <Loader2 size={14} className="animate-spin text-muted" />}
        </div>

        {open && suggestions.length > 0 && (
          <div className="absolute left-0 top-full z-50 mt-1 w-full rounded-md border border-border-soft bg-surface shadow-lg">
            {suggestions.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => handleSelect(s)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-border-soft cursor-pointer"
              >
                <CompanyIcon icon={s.icon} alt={s.name} size={20} />
                <span>{s.name}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
