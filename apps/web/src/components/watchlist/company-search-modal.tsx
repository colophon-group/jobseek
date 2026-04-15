"use client";

import { useState, useRef, useEffect, useMemo } from "react";
import Image from "next/image";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Search, Building2, Loader2, Check, ChevronDown } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import {
  searchCompaniesForWatchlist,
  suggestIndustries,
  type CompanyListEntry,
  type IndustrySuggestion,
} from "@/lib/actions/company";
import { useInfiniteScroll } from "@/lib/use-infinite-scroll";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { RequestCompanyPrompt } from "@/components/search/request-company";
import { useStarredCompanies } from "@/components/StarredCompaniesProvider";

type SelectedCompany = { id: string; name: string; slug: string; icon: string | null };

const BATCH = 20;

export interface CompanySearchFilters {
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  salaryMin?: number;
  salaryMax?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages?: string[];
}

export function CompanySearchModal({
  open,
  onOpenChange,
  selected,
  onToggle,
  onClearAll,
  locale,
  watchlistFilters,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selected: SelectedCompany[];
  onToggle: (company: SelectedCompany) => void;
  onClearAll?: () => void;
  locale: string;
  watchlistFilters?: CompanySearchFilters;
}) {
  const { t } = useLingui();
  const { starredIds } = useStarredCompanies();
  const [query, setQuery] = useState("");
  const [companies, setCompanies] = useState<CompanyListEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [exhausted, setExhausted] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const inputRef = useRef<HTMLInputElement>(null);

  // Industry selector state
  const [industries, setIndustries] = useState<IndustrySuggestion[]>([]);
  const [selectedIndustry, setSelectedIndustry] = useState<IndustrySuggestion | null>(null);
  const [industryQuery, setIndustryQuery] = useState("");
  const [industryOpen, setIndustryOpen] = useState(false);
  const industryRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const selectedIds = useMemo(() => new Set(selected.map((c) => c.id)), [selected]);

  // Load industries on mount
  useEffect(() => {
    if (open) {
      suggestIndustries({ locale }).then(setIndustries);
    }
  }, [open, locale]);

  // Filter industries by query
  const filteredIndustries = useMemo(() => {
    if (!industryQuery.trim()) return industries;
    const q = industryQuery.trim().toLowerCase();
    return industries.filter((i) => i.name.toLowerCase().includes(q));
  }, [industries, industryQuery]);

  // Close industry dropdown on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (industryRef.current && !industryRef.current.contains(e.target as Node)) {
        setIndustryOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  // Reset and load when modal opens
  useEffect(() => {
    if (open) {
      setQuery("");
      setSelectedIndustry(null);
      setIndustryQuery("");
      fetchPage(0);
      setTimeout(() => inputRef.current?.focus(), 100);
    } else {
      setCompanies([]);
      setTotal(0);
      setExhausted(false);
    }
  }, [open]);

  function fetchPage(offset: number) {
    setLoading(true);
    searchCompaniesForWatchlist({
      query: query || undefined,
      industryId: selectedIndustry?.id,
      locale,
      offset,
      limit: BATCH,
      ...watchlistFilters,
      starredCompanyIds: starredIds,
    })
      .then(({ companies: c, total: t }) => {
        if (offset === 0) {
          setCompanies(c);
        } else {
          setCompanies((prev) => {
            const seen = new Set(prev.map((p) => p.id));
            return [...prev, ...c.filter((cc) => !seen.has(cc.id))];
          });
        }
        setTotal(t);
        setExhausted(offset + c.length >= t);
      })
      .finally(() => setLoading(false));
  }

  // Re-search on query or industry change (debounced)
  useEffect(() => {
    if (!open) return;
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => fetchPage(0), query.length > 0 ? 250 : 0);
    return () => clearTimeout(timerRef.current);
  }, [query, selectedIndustry, open]);

  async function handleLoadMore() {
    const { companies: more, total: newTotal } = await searchCompaniesForWatchlist({
      query: query || undefined,
      industryId: selectedIndustry?.id,
      locale,
      offset: companies.length,
      limit: BATCH,
      ...watchlistFilters,
      starredCompanyIds: starredIds,
    });
    setTotal(newTotal);
    if (more.length > 0) {
      setCompanies((prev) => {
        const seen = new Set(prev.map((c) => c.id));
        return [...prev, ...more.filter((c) => !seen.has(c.id))];
      });
    }
    if (more.length < BATCH) setExhausted(true);
  }

  const { sentinelRef, isLoading: isLoadingMore } = useInfiniteScroll({ hasMore: !exhausted, load: handleLoadMore, root: scrollRef });

  // Server already filters out zero-match companies
  const visibleCompanies = companies;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border-soft bg-surface shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
          aria-describedby={undefined}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-divider px-5 py-4">
            <Dialog.Title className="text-base font-semibold">
              <Trans id="watchlists.companyModal.title" comment="Title for add companies modal">
                Add companies
              </Trans>
              <span className="ml-2 text-xs font-normal text-muted">{total}</span>
            </Dialog.Title>
            <div className="flex items-center gap-2">
              {onClearAll && selected.length > 0 && (
                <button
                  type="button"
                  onClick={onClearAll}
                  className="text-xs text-muted transition-colors hover:text-foreground cursor-pointer"
                >
                  {t({ id: "watchlists.companyModal.clearAll", comment: "Clear all selected companies button", message: "Clear all" })}
                </button>
              )}
              <Dialog.Close asChild>
                <button className="rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground cursor-pointer">
                  <X size={16} />
                </button>
              </Dialog.Close>
            </div>
          </div>

          {/* Search + Industry */}
          <div className="border-b border-divider px-5 py-3">
            <div className="flex gap-2">
              {/* Search input */}
              <div className="flex flex-1 items-center gap-2 rounded-md border border-border-soft px-3 py-2">
                <Search size={14} className="shrink-0 text-muted" />
                <input
                  ref={inputRef}
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={t({
                    id: "watchlists.companyModal.searchPlaceholder",
                    comment: "Placeholder for company search in modal",
                    message: "Search companies...",
                  })}
                  className="w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted"
                />
                {loading && companies.length > 0 && <Loader2 size={14} className="animate-spin text-muted" />}
              </div>

              {/* Industry selector */}
              <div ref={industryRef} className="relative">
                <button
                  type="button"
                  onClick={() => setIndustryOpen((v) => !v)}
                  className="flex h-full items-center gap-1.5 rounded-md border border-border-soft px-3 py-2 text-sm text-muted transition-colors hover:border-primary/30 hover:text-foreground cursor-pointer"
                >
                  <span className="max-w-[100px] truncate">
                    {selectedIndustry?.name ?? t({ id: "watchlists.companyModal.industry", comment: "Industry filter label", message: "Industry" })}
                  </span>
                  <ChevronDown size={12} />
                </button>

                {industryOpen && (
                  <div className="absolute right-0 top-full z-50 mt-1 w-56 rounded-md border border-border-soft bg-surface shadow-lg">
                    <div className="border-b border-divider px-3 py-2">
                      <input
                        type="text"
                        value={industryQuery}
                        onChange={(e) => setIndustryQuery(e.target.value)}
                        placeholder={t({ id: "watchlists.companyModal.industrySearch", comment: "Placeholder for industry filter search", message: "Filter industries..." })}
                        className="w-full bg-transparent text-sm outline-none placeholder:text-muted"
                        autoFocus
                      />
                    </div>
                    <div className="max-h-48 overflow-y-auto scrollbar-hide py-1">
                      {selectedIndustry && (
                        <button
                          type="button"
                          onClick={() => { setSelectedIndustry(null); setIndustryOpen(false); setIndustryQuery(""); }}
                          className="flex w-full cursor-pointer items-center px-3 py-1.5 text-left text-sm text-muted transition-colors hover:bg-border-soft"
                        >
                          {t({ id: "watchlists.companyModal.allIndustries", comment: "Option to show all industries", message: "All industries" })}
                        </button>
                      )}
                      {filteredIndustries.map((ind) => (
                        <button
                          key={ind.id}
                          type="button"
                          onClick={() => { setSelectedIndustry(ind); setIndustryOpen(false); setIndustryQuery(""); }}
                          className={`flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors ${
                            selectedIndustry?.id === ind.id ? "bg-primary/10 text-primary" : "hover:bg-border-soft"
                          }`}
                        >
                          <span className="flex-1">{ind.name}</span>
                          {selectedIndustry?.id === ind.id && <Check size={14} className="shrink-0 text-primary" />}
                        </button>
                      ))}
                      {filteredIndustries.length === 0 && (
                        <p className="px-3 py-2 text-xs text-muted">
                          <Trans id="watchlists.companyModal.noIndustries" comment="No industries match search">
                            No industries found.
                          </Trans>
                        </p>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Company list */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto">
            {loading && companies.length === 0 ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={20} className="animate-spin text-muted" />
              </div>
            ) : visibleCompanies.length === 0 && !loading ? (
              <div className="px-5">
                <RequestCompanyPrompt />
              </div>
            ) : (
              <div className="divide-y divide-divider">
                {visibleCompanies.map((c) => {
                  const isSelected = selectedIds.has(c.id);
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => onToggle(c)}
                      className={`flex w-full cursor-pointer items-start gap-3 px-5 py-3 text-left transition-colors ${
                        isSelected ? "bg-primary/5" : "hover:bg-border-soft"
                      }`}
                    >
                      {c.icon ? (
                        <Image
                          src={c.icon}
                          alt={c.name}
                          width={32}
                          height={32}
                          className="mt-0.5 size-8 shrink-0 rounded"
                        />
                      ) : (
                        <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded bg-border-soft text-muted">
                          <Building2 size={16} />
                        </div>
                      )}

                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className={`text-sm font-medium ${isSelected ? "text-primary" : ""}`}>
                            {c.name}
                          </span>
                          <span className="text-xs text-muted">
                            {c.activeMatches} active · {c.yearMatches} in the last year
                          </span>
                        </div>
                        {c.description && (
                          <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-muted">
                            {c.description}
                          </p>
                        )}
                      </div>

                      {isSelected && (
                        <Check size={16} className="mt-1 shrink-0 text-primary" />
                      )}
                    </button>
                  );
                })}

                {!exhausted && <InfiniteScrollSentinel sentinelRef={sentinelRef} isLoading={isLoadingMore} />}

                {/* End of list prompt */}
                {exhausted && visibleCompanies.length > 0 && (
                  <div className="px-5">
                    <RequestCompanyPrompt />
                  </div>
                )}
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
