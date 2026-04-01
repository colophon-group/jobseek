"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Search, MapPin, Building2, ArrowRight, Briefcase, BarChart3, Code2 } from "lucide-react";
import Image from "next/image";
import { useLingui } from "@lingui/react/macro";
import { suggestLocations } from "@/lib/actions/locations";
import type { LocationSuggestion } from "@/lib/actions/locations";
import { suggestCompanies } from "@/lib/actions/company";
import type { CompanySuggestion } from "@/lib/actions/company";
import { suggestOccupations, suggestSeniorities, suggestTechnologies } from "@/lib/actions/taxonomy";
import type { TaxonomySuggestion } from "@/lib/actions/taxonomy";
import { parseSearchFilters } from "@/lib/actions/search-input";
import type { SelectedLocation } from "@/components/search/location-pills";
import { buildFilteredPath } from "@/lib/search/query-params";
import { useSearchStateStore, usePageActions } from "@/components/SearchStateProvider";
import { useLocalePath } from "@/lib/useLocalePath";

type SuggestionItem =
  | { kind: "occupation"; data: TaxonomySuggestion }
  | { kind: "seniority"; data: TaxonomySuggestion }
  | { kind: "technology"; data: TaxonomySuggestion }
  | { kind: "location"; data: LocationSuggestion }
  | { kind: "company"; data: CompanySuggestion };

interface SearchBarProps {
  /** Direct callback for location adds (used on the search page for mobile). */
  onAddLocation?: (location: SelectedLocation) => void;
  onAddOccupation?: (occupation: { id: number; slug: string; name: string }) => void;
  onAddSeniority?: (seniority: { id: number; slug: string; name: string }) => void;
  onAddTechnology?: (tech: { id: number; slug: string; name: string }) => void;
  onSubmitSearch?: (
    keywords: string[],
    locations: SelectedLocation[],
    occupations?: { id: number; slug: string; name: string }[],
    seniorities?: { id: number; slug: string; name: string }[],
    technologies?: { id: number; slug: string; name: string }[],
  ) => void;
  locale?: string;
  keywords?: string[];
  locations?: SelectedLocation[];
  occupations?: { id: number; slug: string; name: string }[];
  seniorities?: { id: number; slug: string; name: string }[];
  technologies?: { id: number; slug: string; name: string }[];
  userLat?: number;
  userLng?: number;
  className?: string;
  placeholder?: string;
}

export function SearchBar({
  onAddLocation,
  onAddOccupation,
  onAddSeniority,
  onAddTechnology,
  onSubmitSearch: _onSubmitSearch,
  locale: localeProp,
  keywords: keywordsProp,
  locations: locationsProp,
  occupations: occupationsProp,
  seniorities: senioritiesProp,
  technologies: technologiesProp,
  userLat: serverLat,
  userLng: serverLng,
  className,
  placeholder: placeholderProp,
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
  const [occupationResults, setOccupationResults] = useState<TaxonomySuggestion[]>([]);
  const [seniorityResults, setSeniorityResults] = useState<TaxonomySuggestion[]>([]);
  const [technologyResults, setTechnologyResults] = useState<TaxonomySuggestion[]>([]);
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

  // Request browser geolocation once per session if server didn't provide coords
  useEffect(() => {
    if (serverLat != null || !navigator.geolocation) return;
    const cached = sessionStorage.getItem("browser-geo");
    if (cached) {
      setBrowserGeo(JSON.parse(cached));
      return;
    }
    if (sessionStorage.getItem("browser-geo-asked")) return;
    sessionStorage.setItem("browser-geo-asked", "1");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const geo = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        sessionStorage.setItem("browser-geo", JSON.stringify(geo));
        setBrowserGeo(geo);
      },
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
  const selectedOccupationIds = new Set((occupationsProp ?? []).map((o) => o.id));
  const selectedSeniorityIds = new Set((senioritiesProp ?? []).map((s) => s.id));
  const selectedTechnologyIds = new Set((technologiesProp ?? []).map((t) => t.id));

  // Build flat list for keyboard navigation
  const allSuggestions: SuggestionItem[] = [
    ...occupationResults.map((s): SuggestionItem => ({ kind: "occupation", data: s })),
    ...seniorityResults.map((s): SuggestionItem => ({ kind: "seniority", data: s })),
    ...technologyResults.map((s): SuggestionItem => ({ kind: "technology", data: s })),
    ...locationResults.map((s): SuggestionItem => ({ kind: "location", data: s })),
    ...companyResults.map((s): SuggestionItem => ({ kind: "company", data: s })),
  ];

  const fetchSuggestions = useCallback(
    (query: string) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (query.trim().length < 2) {
        setLocationResults([]);
        setCompanyResults([]);
        setOccupationResults([]);
        setSeniorityResults([]);
        setTechnologyResults([]);
        setIsOpen(false);
        return;
      }
      debounceRef.current = setTimeout(async () => {
        const [locs, companies, occs, sens, techs] = await Promise.all([
          suggestLocations({ query, locale: lang, userLat, userLng }),
          suggestCompanies({ query }),
          suggestOccupations({ query, locale: lang }),
          suggestSeniorities({ query, locale: lang }),
          suggestTechnologies({ query, locale: lang }),
        ]);
        // Filter out already-selected items
        const filteredLocs = selectedLocationIds
          ? locs.filter((r) => !selectedLocationIds.has(r.id))
          : locs.filter((r) => !selectedLocationSlugs.has(r.slug));
        const filteredOccs = occs.filter((r) => !selectedOccupationIds.has(r.id));
        const filteredSens = sens.filter((r) => !selectedSeniorityIds.has(r.id));
        const filteredTechs = techs.filter((r) => !selectedTechnologyIds.has(r.id));
        setLocationResults(filteredLocs);
        setCompanyResults(companies);
        setOccupationResults(filteredOccs);
        setSeniorityResults(filteredSens);
        setTechnologyResults(filteredTechs);
        setIsOpen(filteredLocs.length > 0 || companies.length > 0 || filteredOccs.length > 0 || filteredSens.length > 0 || filteredTechs.length > 0);
        setActiveIndex(-1);
      }, 200);
    },
    [lang, userLat, userLng, selectedLocationIds, selectedLocationSlugs, selectedOccupationIds, selectedSeniorityIds, selectedTechnologyIds],
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

        if (onAddLocation) {
          onAddLocation(loc);
        } else {
          const pageActions = getPageActions();
          if (pageActions) {
            pageActions.addLocation(loc);
          } else {
            const locSlugs = [...currentLocationSlugs, item.data.slug].join(",");
            const p = new URLSearchParams();
            const q = currentKeywords.join(",");
            if (q) p.set("q", q);
            if (locSlugs) p.set("loc", locSlugs);
            const qs = p.toString();
            router.push(lp(`/explore${qs ? `?${qs}` : ""}`));
          }
        }
      } else if (item.kind === "occupation") {
        const occ = { id: item.data.id, slug: item.data.slug, name: item.data.name };
        if (onAddOccupation) {
          onAddOccupation(occ);
        } else {
          const pageActions = getPageActions();
          if (pageActions) {
            pageActions.addOccupation(occ);
          } else {
            const p = new URLSearchParams(searchParams.toString());
            const existing = p.get("occ");
            p.set("occ", existing ? `${existing},${occ.slug}` : occ.slug);
            router.push(lp(`/explore?${p.toString()}`));
          }
        }
      } else if (item.kind === "seniority") {
        const sen = { id: item.data.id, slug: item.data.slug, name: item.data.name };
        if (onAddSeniority) {
          onAddSeniority(sen);
        } else {
          const pageActions = getPageActions();
          if (pageActions) {
            pageActions.addSeniority(sen);
          } else {
            const p = new URLSearchParams(searchParams.toString());
            const existing = p.get("sen");
            p.set("sen", existing ? `${existing},${sen.slug}` : sen.slug);
            router.push(lp(`/explore?${p.toString()}`));
          }
        }
      } else if (item.kind === "technology") {
        const tech = { id: item.data.id, slug: item.data.slug, name: item.data.name };
        if (onAddTechnology) {
          onAddTechnology(tech);
        } else {
          const pageActions = getPageActions();
          if (pageActions?.addTechnology) {
            pageActions.addTechnology(tech);
          } else {
            const p = new URLSearchParams(searchParams.toString());
            const existing = p.get("tech");
            p.set("tech", existing ? `${existing},${tech.slug}` : tech.slug);
            router.push(lp(`/explore?${p.toString()}`));
          }
        }
      } else {
        // Company: navigate to company page, preserving current filters
        const pageActions = getPageActions();
        const kws = keywordsProp ?? pageActions?.getKeywords() ?? currentKeywords;
        const locs = locationsProp ?? pageActions?.getLocations() ?? [];
        const occs = occupationsProp ?? pageActions?.getOccupations() ?? [];
        const sens = senioritiesProp ?? pageActions?.getSeniorities() ?? [];
        const techs = technologiesProp ?? pageActions?.getTechnologies?.() ?? [];
        const href = buildFilteredPath(
          lp(`/company/${item.data.slug}`),
          kws,
          locs,
          undefined,
          occs,
          sens,
          techs,
        );
        router.push(href);
      }
      setInputValue("");
      setLocationResults([]);
      setCompanyResults([]);
      setOccupationResults([]);
      setSeniorityResults([]);
      setTechnologyResults([]);
      setIsOpen(false);
      setActiveIndex(-1);
      if (item.kind !== "company") {
        inputRef.current?.focus();
      }
    },
    [onAddLocation, onAddOccupation, onAddSeniority, onAddTechnology, getPageActions, router, lp, searchParams, currentKeywords, currentLocationSlugs, keywordsProp, locationsProp, occupationsProp, senioritiesProp, technologiesProp],
  );

  const submitFreeTextSearch = useCallback(() => {
    const input = inputValue.trim();
    if (!input) return;

    // Clear input immediately for instant feedback
    setInputValue("");
    setLocationResults([]);
    setCompanyResults([]);
    setOccupationResults([]);
    setSeniorityResults([]);
    setTechnologyResults([]);
    setIsOpen(false);
    setActiveIndex(-1);

    // Snapshot existing filters synchronously before async work
    const pageActions = getPageActions();
    const existingKw = keywordsProp ?? pageActions?.getKeywords() ?? currentKeywords;
    const existingLocs = locationsProp ?? pageActions?.getLocations() ?? [];
    const existingOccs = occupationsProp ?? pageActions?.getOccupations() ?? [];
    const existingSens = senioritiesProp ?? pageActions?.getSeniorities() ?? [];
    const existingTechs = technologiesProp ?? pageActions?.getTechnologies?.() ?? [];

    // Parse input then navigate via URL.
    // We always use router.push so the server component handles the search
    // in a single request, avoiding sequential server-action issues that can
    // cause the client-side transition to hang indefinitely.
    parseSearchFilters({ q: input, locale: lang, userLat, userLng })
      .then((parsed) => {
        const kwSet = new Set(existingKw.map((k) => k.toLowerCase()));
        const mergedKw = [...existingKw, ...parsed.keywords.filter((k) => !kwSet.has(k.toLowerCase()))];
        const locIdSet = new Set(existingLocs.map((l) => l.id));
        const mergedLocs = [...existingLocs, ...parsed.locations.filter((l) => !locIdSet.has(l.id))];
        const occIdSet = new Set(existingOccs.map((o) => o.id));
        const mergedOccs = [...existingOccs, ...parsed.occupations.filter((o) => !occIdSet.has(o.id))];
        const senIdSet = new Set(existingSens.map((s) => s.id));
        const mergedSens = [...existingSens, ...parsed.seniorities.filter((s) => !senIdSet.has(s.id))];
        const techIdSet = new Set(existingTechs.map((t) => t.id));
        const mergedTechs = [...existingTechs, ...(parsed.technologies ?? []).filter((t) => !techIdSet.has(t.id))];

        router.push(buildFilteredPath(lp("/explore"), mergedKw, mergedLocs, undefined, mergedOccs, mergedSens, mergedTechs));
      })
      .catch(() => {
        // Fallback: treat raw input as a keyword and navigate
        const mergedKw = [...existingKw, input];
        router.push(buildFilteredPath(lp("/explore"), mergedKw, existingLocs, undefined, existingOccs, existingSens, existingTechs));
      });
  }, [inputValue, lang, userLat, userLng, getPageActions, router, lp, keywordsProp, locationsProp, occupationsProp, senioritiesProp, technologiesProp, currentKeywords]);

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

  const reactivePageActions = usePageActions();
  const placeholder = placeholderProp ?? reactivePageActions?.placeholder ?? t({
    id: "search.bar.placeholder",
    comment: "Placeholder for the main search bar",
    message: "Search...",
  });

  // Compute flat indices for each section
  let flatIdx = 0;
  const occStartIndex = flatIdx;
  flatIdx += occupationResults.length;
  const senStartIndex = flatIdx;
  flatIdx += seniorityResults.length;
  const techStartIndex = flatIdx;
  flatIdx += technologyResults.length;
  const locStartIndex = flatIdx;
  flatIdx += locationResults.length;
  const companyStartIndex = flatIdx;

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
          className="absolute left-0 top-full z-50 mt-1 max-h-80 w-full min-w-64 overflow-y-auto scrollbar-hide rounded-lg border border-border-soft bg-surface shadow-lg"
        >
          {occupationResults.length > 0 && (
            <>
              <div className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted">
                {t({
                  id: "search.bar.roles",
                  comment: "Section header for occupation suggestions in search bar",
                  message: "Roles",
                })}
              </div>
              {occupationResults.map((s, i) => {
                const fi = occStartIndex + i;
                return (
                  <div
                    key={`occ-${s.id}`}
                    id={`search-option-${fi}`}
                    role="option"
                    aria-selected={fi === activeIndex}
                    data-suggestion
                    onMouseDown={(e) => {
                      e.preventDefault();
                      selectItem({ kind: "occupation", data: s });
                    }}
                    onMouseEnter={() => setActiveIndex(fi)}
                    className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                      fi === activeIndex ? "bg-primary/10" : "hover:bg-primary/5"
                    }`}
                  >
                    <Briefcase size={14} className="shrink-0 text-muted" />
                    <span className="min-w-0 flex-1 font-medium">{s.name}</span>
                  </div>
                );
              })}
            </>
          )}

          {seniorityResults.length > 0 && (
            <>
              <div className={`px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted ${occupationResults.length > 0 ? "border-t border-border-soft" : ""}`}>
                {t({
                  id: "search.bar.level",
                  comment: "Section header for seniority suggestions in search bar",
                  message: "Level",
                })}
              </div>
              {seniorityResults.map((s, i) => {
                const fi = senStartIndex + i;
                return (
                  <div
                    key={`sen-${s.id}`}
                    id={`search-option-${fi}`}
                    role="option"
                    aria-selected={fi === activeIndex}
                    data-suggestion
                    onMouseDown={(e) => {
                      e.preventDefault();
                      selectItem({ kind: "seniority", data: s });
                    }}
                    onMouseEnter={() => setActiveIndex(fi)}
                    className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                      fi === activeIndex ? "bg-primary/10" : "hover:bg-primary/5"
                    }`}
                  >
                    <BarChart3 size={14} className="shrink-0 text-muted" />
                    <span className="min-w-0 flex-1 font-medium">{s.name}</span>
                  </div>
                );
              })}
            </>
          )}

          {technologyResults.length > 0 && (
            <>
              <div className={`px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted ${(occupationResults.length > 0 || seniorityResults.length > 0) ? "border-t border-border-soft" : ""}`}>
                {t({
                  id: "search.bar.technologies",
                  comment: "Section header for technology suggestions in search bar",
                  message: "Technologies",
                })}
              </div>
              {technologyResults.map((s, i) => {
                const fi = techStartIndex + i;
                return (
                  <div
                    key={`tech-${s.id}`}
                    id={`search-option-${fi}`}
                    role="option"
                    aria-selected={fi === activeIndex}
                    data-suggestion
                    onMouseDown={(e) => {
                      e.preventDefault();
                      selectItem({ kind: "technology", data: s });
                    }}
                    onMouseEnter={() => setActiveIndex(fi)}
                    className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                      fi === activeIndex ? "bg-primary/10" : "hover:bg-primary/5"
                    }`}
                  >
                    <Code2 size={14} className="shrink-0 text-muted" />
                    <span className="min-w-0 flex-1 font-medium">{s.name}</span>
                  </div>
                );
              })}
            </>
          )}

          {locationResults.length > 0 && (
            <>
              <div className={`px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted ${(occupationResults.length > 0 || seniorityResults.length > 0 || technologyResults.length > 0) ? "border-t border-border-soft" : ""}`}>
                {t({
                  id: "search.bar.locations",
                  comment: "Section header for location suggestions in search bar",
                  message: "Locations",
                })}
              </div>
              {locationResults.map((s, i) => {
                const fi = locStartIndex + i;
                return (
                  <div
                    key={`loc-${s.id}`}
                    id={`search-option-${fi}`}
                    role="option"
                    aria-selected={fi === activeIndex}
                    data-suggestion
                    onMouseDown={(e) => {
                      e.preventDefault();
                      selectItem({ kind: "location", data: s });
                    }}
                    onMouseEnter={() => setActiveIndex(fi)}
                    className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                      fi === activeIndex ? "bg-primary/10" : "hover:bg-primary/5"
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
                );
              })}
            </>
          )}

          {companyResults.length > 0 && (
            <>
              <div className={`px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted ${(locationResults.length > 0 || occupationResults.length > 0 || seniorityResults.length > 0 || technologyResults.length > 0) ? "border-t border-border-soft" : ""}`}>
                {t({
                  id: "search.bar.companies",
                  comment: "Section header for company suggestions in search bar",
                  message: "Companies",
                })}
              </div>
              {companyResults.map((c, i) => {
                const fi = companyStartIndex + i;
                return (
                  <div
                    key={`co-${c.id}`}
                    id={`search-option-${fi}`}
                    role="option"
                    aria-selected={fi === activeIndex}
                    data-suggestion
                    onMouseDown={(e) => {
                      e.preventDefault();
                      selectItem({ kind: "company", data: c });
                    }}
                    onMouseEnter={() => setActiveIndex(fi)}
                    className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                      fi === activeIndex ? "bg-primary/10" : "hover:bg-primary/5"
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
