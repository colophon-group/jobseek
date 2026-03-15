"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Search, MapPin, Building2, ArrowRight } from "lucide-react";
import Image from "next/image";
import { useLingui } from "@lingui/react/macro";
import { suggestLocations } from "@/lib/actions/locations";
import type { LocationSuggestion } from "@/lib/actions/locations";
import { suggestCompanies } from "@/lib/actions/company";
import type { CompanySuggestion } from "@/lib/actions/company";
import { parseSearchFilters } from "@/lib/actions/search-input";
import type { SelectedLocation } from "@/components/search/location-pills";
import { buildFilteredPath } from "@/lib/search/query-params";
import { useSearchStateStore } from "@/components/SearchStateProvider";
import { useLocalePath } from "@/lib/useLocalePath";

type SuggestionItem =
  | { kind: "location"; data: LocationSuggestion }
  | { kind: "company"; data: CompanySuggestion };

interface SearchBarProps {
  /** Direct callback for location adds (used on the search page for mobile). */
  onAddLocation?: (location: SelectedLocation) => void;
  onSubmitSearch?: (keywords: string[], locations: SelectedLocation[]) => void;
  locale?: string;
  keywords?: string[];
  locations?: SelectedLocation[];
  userLat?: number;
  userLng?: number;
  className?: string;
}

export function SearchBar({
  onAddLocation,
  onSubmitSearch,
  locale: localeProp,
  keywords: keywordsProp,
  locations: locationsProp,
  userLat: serverLat,
  userLng: serverLng,
  className,
}: SearchBarProps) {
  const { t } = useLingui();
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();
  const lp = useLocalePath();
  const { getPageActions } = useSearchStateStore();

  const lang = localeProp ?? (params.lang as string) ?? "en";

  const [inputValue, setInputValue] = useState("");
  const [locationResults, setLocationResults] = useState<LocationSuggestion[]>([]);
  const [companyResults, setCompanyResults] = useState<CompanySuggestion[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [browserGeo, setBrowserGeo] = useState<{ lat: number; lng: number } | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null);
  const isKeyboardNav = useRef(false);

  const userLat = serverLat ?? browserGeo?.lat;
  const userLng = serverLng ?? browserGeo?.lng;

  // Request browser geolocation once if server didn't provide coords
  useEffect(() => {
    if (serverLat != null || !navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => setBrowserGeo({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      () => {},
      { maximumAge: 600_000, timeout: 5_000 },
    );
  }, [serverLat]);

  // Current filter state: from props if available, otherwise derive from URL
  const currentKeywords = keywordsProp ?? (searchParams.get("q")?.split(",").filter(Boolean) ?? []);
  const currentLocationSlugs = locationsProp
    ? locationsProp.map((l) => l.slug)
    : (searchParams.get("loc")?.split(",").filter(Boolean) ?? []);
  const selectedLocationIds = locationsProp
    ? new Set(locationsProp.map((l) => l.id))
    : null;
  const selectedLocationSlugs = new Set(currentLocationSlugs);

  // Build flat list for keyboard navigation
  const allSuggestions: SuggestionItem[] = [
    ...locationResults.map((s): SuggestionItem => ({ kind: "location", data: s })),
    ...companyResults.map((s): SuggestionItem => ({ kind: "company", data: s })),
  ];

  const fetchSuggestions = useCallback(
    (query: string) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (query.trim().length < 2) {
        setLocationResults([]);
        setCompanyResults([]);
        setIsOpen(false);
        return;
      }
      debounceRef.current = setTimeout(async () => {
        const [locs, companies] = await Promise.all([
          suggestLocations({ query, locale: lang, userLat, userLng }),
          suggestCompanies({ query }),
        ]);
        // Filter out already-selected locations
        const filteredLocs = selectedLocationIds
          ? locs.filter((r) => !selectedLocationIds.has(r.id))
          : locs.filter((r) => !selectedLocationSlugs.has(r.slug));
        setLocationResults(filteredLocs);
        setCompanyResults(companies);
        setIsOpen(filteredLocs.length > 0 || companies.length > 0);
        setActiveIndex(-1);
      }, 200);
    },
    [lang, userLat, userLng, selectedLocationIds, selectedLocationSlugs],
  );

  const selectItem = useCallback(
    (item: SuggestionItem) => {
      if (item.kind === "location") {
        const loc: SelectedLocation = {
          id: item.data.id,
          slug: item.data.slug,
          name: item.data.name,
          type: item.data.type,
          parentName: item.data.parentName,
        };

        // Priority: direct callback > page actions via context > URL navigation
        if (onAddLocation) {
          onAddLocation(loc);
        } else {
          const pageActions = getPageActions();
          if (pageActions) {
            pageActions.addLocation(loc);
          } else {
            // Navigate via URL
            const locSlugs = [...currentLocationSlugs, item.data.slug].join(",");
            const p = new URLSearchParams();
            const q = currentKeywords.join(",");
            if (q) p.set("q", q);
            if (locSlugs) p.set("loc", locSlugs);
            const qs = p.toString();
            router.push(lp(`/app${qs ? `?${qs}` : ""}`));
          }
        }
      } else {
        // Company: navigate to company page, preserving current filters
        const pageActions = getPageActions();
        const kws = keywordsProp ?? pageActions?.getKeywords() ?? currentKeywords;
        const locs = locationsProp ?? pageActions?.getLocations() ?? [];
        const href = buildFilteredPath(
          lp(`/company/${item.data.slug}`),
          kws,
          locs,
        );
        router.push(href);
      }
      setInputValue("");
      setLocationResults([]);
      setCompanyResults([]);
      setIsOpen(false);
      setActiveIndex(-1);
      if (item.kind === "location") {
        inputRef.current?.focus();
      }
    },
    [onAddLocation, getPageActions, router, lp, currentKeywords, currentLocationSlugs, keywordsProp, locationsProp],
  );

  const submitFreeTextSearch = useCallback(async () => {
    const input = inputValue.trim();
    if (!input) return;

    const parsed = await parseSearchFilters({
      q: input,
      locale: lang,
      userLat,
      userLng,
    });

    if (onSubmitSearch) {
      onSubmitSearch(parsed.keywords, parsed.locations);
    } else {
      const pageActions = getPageActions();
      if (pageActions) {
        pageActions.submitSearch(parsed.keywords, parsed.locations);
      } else {
        router.push(buildFilteredPath(lp("/app"), parsed.keywords, parsed.locations));
      }
    }

    setInputValue("");
    setLocationResults([]);
    setCompanyResults([]);
    setIsOpen(false);
    setActiveIndex(-1);
  }, [inputValue, lang, userLat, userLng, onSubmitSearch, getPageActions, router, lp]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      if (!isOpen || allSuggestions.length === 0) return;
      e.preventDefault();
      isKeyboardNav.current = true;
      setActiveIndex((prev) =>
        prev < allSuggestions.length - 1 ? prev + 1 : 0,
      );
    } else if (e.key === "ArrowUp") {
      if (!isOpen || allSuggestions.length === 0) return;
      e.preventDefault();
      isKeyboardNav.current = true;
      setActiveIndex((prev) =>
        prev > 0 ? prev - 1 : allSuggestions.length - 1,
      );
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (activeIndex >= 0 && activeIndex < allSuggestions.length) {
        selectItem(allSuggestions[activeIndex]);
      } else {
        void submitFreeTextSearch();
      }
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
      const items = listRef.current.querySelectorAll("[data-suggestion]");
      const item = items[activeIndex] as HTMLElement;
      item?.scrollIntoView({ block: "nearest" });
    }
    isKeyboardNav.current = false;
  }, [activeIndex]);

  const placeholder = t({
    id: "search.bar.placeholder",
    comment: "Placeholder for the main search bar",
    message: "Search...",
  });

  const companyStartIndex = locationResults.length;

  return (
    <div className={`relative ${className ?? ""}`} ref={containerRef}>
      <div className="flex items-center gap-2 rounded-lg border border-border-soft px-3 py-1.5 transition-colors focus-within:border-primary/40">
        <Search size={16} className="shrink-0 text-muted" />
        <input
          ref={inputRef}
          type="text"
          value={inputValue}
          onChange={(e) => {
            setInputValue(e.target.value);
            fetchSuggestions(e.target.value);
          }}
          onFocus={() => {
            if (allSuggestions.length > 0) setIsOpen(true);
          }}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          className="w-full bg-transparent text-sm outline-none placeholder:text-muted"
          role="combobox"
          aria-expanded={isOpen}
          aria-autocomplete="list"
          aria-activedescendant={
            activeIndex >= 0 ? `search-option-${activeIndex}` : undefined
          }
        />
      </div>

      {isOpen && allSuggestions.length > 0 && (
        <div
          ref={listRef}
          role="listbox"
          className="absolute left-0 top-full z-50 mt-1 max-h-80 w-full min-w-64 overflow-auto rounded-lg border border-border-soft bg-surface shadow-lg"
        >
          {locationResults.length > 0 && (
            <>
              <div className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted">
                {t({
                  id: "search.bar.locations",
                  comment: "Section header for location suggestions in search bar",
                  message: "Locations",
                })}
              </div>
              {locationResults.map((s, i) => (
                <div
                  key={`loc-${s.id}`}
                  id={`search-option-${i}`}
                  role="option"
                  aria-selected={i === activeIndex}
                  data-suggestion
                  onMouseDown={(e) => {
                    e.preventDefault();
                    selectItem({ kind: "location", data: s });
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
                  </div>
                </div>
              ))}
            </>
          )}

          {companyResults.length > 0 && (
            <>
              <div className={`px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted ${locationResults.length > 0 ? "border-t border-border-soft" : ""}`}>
                {t({
                  id: "search.bar.companies",
                  comment: "Section header for company suggestions in search bar",
                  message: "Companies",
                })}
              </div>
              {companyResults.map((c, i) => {
                const flatIndex = companyStartIndex + i;
                return (
                  <div
                    key={`co-${c.id}`}
                    id={`search-option-${flatIndex}`}
                    role="option"
                    aria-selected={flatIndex === activeIndex}
                    data-suggestion
                    onMouseDown={(e) => {
                      e.preventDefault();
                      selectItem({ kind: "company", data: c });
                    }}
                    onMouseEnter={() => setActiveIndex(flatIndex)}
                    className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                      flatIndex === activeIndex ? "bg-primary/10" : "hover:bg-primary/5"
                    }`}
                  >
                    {c.icon ? (
                      <Image
                        src={c.icon}
                        alt=""
                        width={16}
                        height={16}
                        className="size-4 shrink-0 rounded-sm"
                      />
                    ) : (
                      <Building2 size={14} className="shrink-0 text-muted" />
                    )}
                    <span className="min-w-0 flex-1 font-medium">{c.name}</span>
                    <ArrowRight size={12} className="shrink-0 text-muted" />
                  </div>
                );
              })}
            </>
          )}
        </div>
      )}
    </div>
  );
}
