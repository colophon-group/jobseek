"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { X, MapPin, Search, ChevronDown, ChevronUp, Globe } from "lucide-react";
import { useLingui, Trans } from "@lingui/react/macro";
import { suggestLocations } from "@/lib/actions/locations";
import type { LocationSuggestion } from "@/lib/actions/locations";
import { LocationModal } from "./location-modal";

export type FilterItem =
  | { kind: "location"; id: number; slug: string; name: string; type: string }
  | { kind: "keyword"; value: string };

interface FilterBarProps {
  suggestedLocations?: { id: number; slug: string; name: string; type: string; count: number }[];
  totalLocationCount?: number;
  companyId?: string;
  filters: FilterItem[];
  onFiltersChange: (filters: FilterItem[]) => void;
  locale: string;
  userLat?: number;
  userLng?: number;
  placeholder?: string;
}

const TYPE_LABELS: Record<string, string> = {
  macro: "Region",
  country: "Country",
  region: "Region",
  city: "City",
};

export function FilterBar({
  suggestedLocations,
  totalLocationCount,
  companyId,
  filters,
  onFiltersChange,
  locale,
  userLat,
  userLng,
  placeholder: customPlaceholder,
}: FilterBarProps) {
  const { t } = useLingui();
  const [inputValue, setInputValue] = useState("");
  const [suggestions, setSuggestions] = useState<LocationSuggestion[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null);
  const isKeyboardNav = useRef(false);

  const activeLocationIds = new Set(
    filters.filter((f) => f.kind === "location").map((f) => f.id),
  );

  const placeholder = customPlaceholder ?? t({
    id: "company.page.filterPlaceholder",
    comment: "Placeholder for the company page filter search bar",
    message: "Add location or keyword filter...",
  });

  const fetchSuggestions = useCallback(
    (query: string) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (query.trim().length < 2) {
        setSuggestions([]);
        setIsOpen(false);
        return;
      }
      debounceRef.current = setTimeout(async () => {
        const results = await suggestLocations({
          query,
          locale,
          userLat,
          userLng,
        });
        const filtered = results.filter((r) => !activeLocationIds.has(r.id));
        setSuggestions(filtered);
        setIsOpen(filtered.length > 0);
        setActiveIndex(-1);
      }, 200);
    },
    [locale, userLat, userLng, activeLocationIds.size, filters],
  );

  const addLocationFilter = useCallback(
    (loc: { id: number; slug: string; name: string; type: string }) => {
      if (activeLocationIds.has(loc.id)) return;
      onFiltersChange([...filters, { kind: "location", id: loc.id, slug: loc.slug, name: loc.name, type: loc.type }]);
    },
    [filters, onFiltersChange, activeLocationIds],
  );

  const removeFilter = useCallback(
    (filter: FilterItem) => {
      if (filter.kind === "location") {
        onFiltersChange(filters.filter((f) => !(f.kind === "location" && f.id === filter.id)));
      } else {
        onFiltersChange(filters.filter((f) => !(f.kind === "keyword" && f.value === filter.value)));
      }
    },
    [filters, onFiltersChange],
  );

  const toggleSuggestedLocation = useCallback(
    (loc: { id: number; slug: string; name: string; type: string }) => {
      if (activeLocationIds.has(loc.id)) {
        onFiltersChange(filters.filter((f) => !(f.kind === "location" && f.id === loc.id)));
      } else {
        onFiltersChange([...filters, { kind: "location", id: loc.id, slug: loc.slug, name: loc.name, type: loc.type }]);
      }
    },
    [filters, onFiltersChange, activeLocationIds],
  );

  const selectSuggestion = useCallback(
    (suggestion: LocationSuggestion) => {
      addLocationFilter({ id: suggestion.id, slug: suggestion.slug, name: suggestion.name, type: suggestion.type });
      setInputValue("");
      setSuggestions([]);
      setIsOpen(false);
      setActiveIndex(-1);
      inputRef.current?.focus();
    },
    [addLocationFilter],
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (isOpen && activeIndex >= 0 && activeIndex < suggestions.length) {
        selectSuggestion(suggestions[activeIndex]);
      } else {
        const trimmed = inputValue.trim();
        if (trimmed && !filters.some((f) => f.kind === "keyword" && f.value.toLowerCase() === trimmed.toLowerCase())) {
          onFiltersChange([...filters, { kind: "keyword", value: trimmed }]);
          setInputValue("");
          setSuggestions([]);
          setIsOpen(false);
        }
      }
      return;
    }
    if (!isOpen || suggestions.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      isKeyboardNav.current = true;
      setActiveIndex((prev) => (prev < suggestions.length - 1 ? prev + 1 : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      isKeyboardNav.current = true;
      setActiveIndex((prev) => (prev > 0 ? prev - 1 : suggestions.length - 1));
    } else if (e.key === "Escape") {
      setIsOpen(false);
      setActiveIndex(-1);
    }
  };

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Scroll active item into view (keyboard nav only)
  useEffect(() => {
    if (isKeyboardNav.current && activeIndex >= 0 && listRef.current) {
      const item = listRef.current.children[activeIndex] as HTMLElement;
      item?.scrollIntoView({ block: "nearest" });
    }
    isKeyboardNav.current = false;
  }, [activeIndex]);

  const COLLAPSED_COUNT = 6;
  const [expanded, setExpanded] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const showModal = (totalLocationCount ?? 0) > 15 && !!companyId;

  const keywordFilters = filters.filter((f) => f.kind === "keyword");

  // Non-suggested location filters (added via search input)
  const extraLocationFilters = filters.filter(
    (f): f is FilterItem & { kind: "location" } =>
      f.kind === "location" && !suggestedLocations?.some((sl) => sl.id === f.id),
  );

  // Sort suggested locations: selected first, then by original order
  const sortedSuggested = useMemo(() => {
    if (!suggestedLocations) return [];
    return [...suggestedLocations].sort((a, b) => {
      const aActive = activeLocationIds.has(a.id);
      const bActive = activeLocationIds.has(b.id);
      if (aActive && !bActive) return -1;
      if (!aActive && bActive) return 1;
      return 0;
    });
  }, [suggestedLocations, activeLocationIds]);

  // All pill items: keyword pills + extra location pills + suggested location pills
  // Selected items (keywords, extra locations, active suggested) come first naturally
  // since keyword/extra pills are always "active" and suggested are sorted active-first.
  const totalPillCount = keywordFilters.length + extraLocationFilters.length + sortedSuggested.length;
  const canExpand = totalPillCount > COLLAPSED_COUNT;

  // Build the combined ordered pill list for truncation
  type PillItem =
    | { type: "keyword"; filter: FilterItem & { kind: "keyword" } }
    | { type: "extraLoc"; filter: FilterItem & { kind: "location" } }
    | { type: "suggested"; loc: { id: number; slug: string; name: string; type: string; count: number }; isActive: boolean };

  const allPills = useMemo((): PillItem[] => {
    const items: PillItem[] = [];
    for (const f of keywordFilters) items.push({ type: "keyword", filter: f });
    for (const f of extraLocationFilters) items.push({ type: "extraLoc", filter: f });
    for (const loc of sortedSuggested) items.push({ type: "suggested", loc, isActive: activeLocationIds.has(loc.id) });
    return items;
  }, [keywordFilters, extraLocationFilters, sortedSuggested, activeLocationIds]);

  const visiblePills = expanded ? allPills : allPills.slice(0, COLLAPSED_COUNT);
  const hiddenCount = allPills.length - visiblePills.length;

  return (
    <div ref={containerRef} className="space-y-3">
      {/* Pills */}
      {allPills.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          {visiblePills.map((pill) => {
            if (pill.type === "keyword") {
              return (
                <span
                  key={`kw-${pill.filter.value}`}
                  className="inline-flex items-center gap-1 rounded-full bg-accent/10 px-3 py-1 text-sm text-accent"
                >
                  {pill.filter.value}
                  <button
                    onClick={() => removeFilter(pill.filter)}
                    className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-accent/20 cursor-pointer"
                  >
                    <X size={12} />
                  </button>
                </span>
              );
            }
            if (pill.type === "extraLoc") {
              return (
                <span
                  key={`el-${pill.filter.id}`}
                  className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
                >
                  <MapPin size={12} className="shrink-0" />
                  {pill.filter.name}
                  <button
                    onClick={() => removeFilter(pill.filter)}
                    className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
                  >
                    <X size={12} />
                  </button>
                </span>
              );
            }
            // suggested
            return (
              <button
                key={`sl-${pill.loc.id}`}
                onClick={() => toggleSuggestedLocation(pill.loc)}
                className={`inline-flex cursor-pointer items-center gap-1 rounded-full px-3 py-1 text-sm transition-colors ${
                  pill.isActive
                    ? "bg-primary/10 text-primary"
                    : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                }`}
              >
                {pill.loc.name}
                <span className={`text-xs ${pill.isActive ? "text-primary/70" : "text-muted"}`}>
                  ({pill.loc.count})
                </span>
              </button>
            );
          })}
          {showModal ? (
            <button
              onClick={() => setModalOpen(true)}
              className="inline-flex cursor-pointer items-center gap-1 rounded-full px-2 py-1 text-xs text-muted transition-colors hover:text-foreground"
            >
              <Globe size={12} />
              <Trans id="company.page.allLocations" comment="Button to open all-locations modal on company page">All locations</Trans>
            </button>
          ) : canExpand && (
            <button
              onClick={() => setExpanded((v) => !v)}
              className="inline-flex cursor-pointer items-center gap-1 rounded-full px-2 py-1 text-xs text-muted transition-colors hover:text-foreground"
            >
              {expanded ? (
                <>
                  <Trans id="company.page.showLess" comment="Collapse location pill list">Show less</Trans>
                  <ChevronUp size={12} />
                </>
              ) : (
                <>
                  +{hiddenCount} <Trans id="company.page.showMore" comment="Expand location pill list to show all">more</Trans>
                  <ChevronDown size={12} />
                </>
              )}
            </button>
          )}
        </div>
      )}

      {/* Search input */}
      <div className="relative">
        <div className="flex items-center gap-2 rounded-md border border-border-soft px-3 py-2">
          <Search size={14} className="shrink-0 text-muted" />
          <input
            ref={inputRef}
            type="text"
            value={inputValue}
            onChange={(e) => {
              setInputValue(e.target.value);
              fetchSuggestions(e.target.value);
            }}
            onFocus={() => {
              if (suggestions.length > 0) setIsOpen(true);
            }}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            className="w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted"
            role="combobox"
            aria-expanded={isOpen}
            aria-autocomplete="list"
            aria-activedescendant={activeIndex >= 0 ? `filter-option-${activeIndex}` : undefined}
          />
        </div>
        {isOpen && suggestions.length > 0 && (
          <ul
            ref={listRef}
            role="listbox"
            className="absolute left-0 top-full z-50 mt-1 max-h-60 w-72 overflow-auto rounded-lg border border-border-soft bg-surface shadow-lg"
          >
            {suggestions.map((s, i) => (
              <li
                key={s.id}
                id={`filter-option-${i}`}
                role="option"
                aria-selected={i === activeIndex}
                onMouseDown={(e) => {
                  e.preventDefault();
                  selectSuggestion(s);
                }}
                onMouseEnter={() => setActiveIndex(i)}
                className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                  i === activeIndex ? "bg-primary/10" : "hover:bg-primary/5"
                }`}
              >
                <MapPin size={14} className="shrink-0 text-muted" />
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{s.name}</span>
                  {s.parentName && (
                    <span className="text-muted">, {s.parentName}</span>
                  )}
                  <span className="ml-1.5 text-xs text-muted">
                    {TYPE_LABELS[s.type]}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {showModal && (
        <LocationModal
          open={modalOpen}
          onOpenChange={setModalOpen}
          companyId={companyId}
          locale={locale}
          filters={filters}
          onFiltersChange={onFiltersChange}
        />
      )}
    </div>
  );
}
