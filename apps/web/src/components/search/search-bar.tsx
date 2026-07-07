"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Search,
  MapPin,
  ArrowRight,
  Briefcase,
  BarChart3,
  Code2,
  Sparkles,
  Home,
} from "lucide-react";
import { CompanyIcon } from "@/components/CompanyIcon";
import { useLingui } from "@lingui/react/macro";
import type { LocationSuggestion } from "@/lib/actions/locations";
import type { CompanySuggestion } from "@/lib/actions/company";
import type { TaxonomySuggestion } from "@/lib/actions/taxonomy";
import { parseSearchFilters } from "@/lib/actions/search-input";
import type { SelectedLocation } from "@/lib/search/types";
import { buildFilteredPath, parseWorkModeParam } from "@/lib/search/query-params";
import type { WorkMode } from "@/lib/search/types";
import { useSearchStateStore, usePageActions } from "@/components/providers/SearchStateProvider";
import { useLocalePath } from "@/lib/useLocalePath";
import { ScrollFade } from "@/components/ui/scroll-fade";
import { useBrowserCoordinates } from "@/lib/search/browser-geolocation";
import { SearchBarSuggestionSection } from "@/components/search/search-bar-suggestion-section";
import { matchWorkModes, useSearchBarTypeahead } from "@/components/search/search-bar-typeahead";

type SuggestionItem =
  | { kind: "keyword"; data: { text: string } }
  | { kind: "occupation"; data: TaxonomySuggestion }
  | { kind: "seniority"; data: TaxonomySuggestion }
  | { kind: "technology"; data: TaxonomySuggestion }
  | { kind: "workMode"; data: { value: WorkMode } }
  | { kind: "location"; data: LocationSuggestion }
  | { kind: "company"; data: CompanySuggestion }
  /**
   * Synthetic "Request <query>" entry rendered at the bottom of the
   * dropdown when the user's query has no company match. Activating
   * this item navigates to the company-request landing page with the
   * raw query pre-filled. Owned by issue #2807; the landing page is
   * jobseek#2808.
   */
  | { kind: "request"; data: { query: string } };

interface SearchBarProps {
  /** Direct callback for location adds (used on the search page for mobile). */
  onAddLocation?: (location: SelectedLocation) => void;
  onAddOccupation?: (occupation: { id: number; slug: string; name: string }) => void;
  onAddSeniority?: (seniority: { id: number; slug: string; name: string }) => void;
  onAddTechnology?: (tech: { id: number; slug: string; name: string }) => void;
  /** Direct callback for work-mode adds (issue #2983). When omitted,
   *  falls through to `pageActions.addWorkMode` then a URL push fallback.
   */
  onAddWorkMode?: (mode: WorkMode) => void;
  onSubmitSearch?: (
    keywords: string[],
    locations: SelectedLocation[],
    occupations?: { id: number; slug: string; name: string }[],
    seniorities?: { id: number; slug: string; name: string }[],
    technologies?: { id: number; slug: string; name: string }[],
    workMode?: WorkMode[],
  ) => void;
  locale?: string;
  keywords?: string[];
  locations?: SelectedLocation[];
  occupations?: { id: number; slug: string; name: string }[];
  seniorities?: { id: number; slug: string; name: string }[];
  technologies?: { id: number; slug: string; name: string }[];
  workMode?: WorkMode[];
  languages?: string[];
  companyId?: string;
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
  onAddWorkMode,
  onSubmitSearch: _onSubmitSearch,
  locale: localeProp,
  keywords: keywordsProp,
  locations: locationsProp,
  occupations: occupationsProp,
  seniorities: senioritiesProp,
  technologies: technologiesProp,
  workMode: workModeProp,
  languages: languagesProp,
  companyId,
  userLat: serverLat,
  userLng: serverLng,
  className,
  placeholder: placeholderProp,
}: SearchBarProps) {
  const { t } = useLingui();
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const lp = useLocalePath();
  const { getPageActions } = useSearchStateStore();

  const lang = localeProp ?? (params.lang as string) ?? "en";

  // Suppress cross-company suggestions whenever the search bar is
  // rendered inside a company page — either via the explicit
  // `companyId` prop (in-toolbar mobile bar) or detected from the
  // pathname (global header bar on `/[lang]/company/[slug]`).
  const isOnCompanyRoute = /^\/[a-z]{2}\/company\/[^/]+$/.test(pathname ?? "");
  const scopedToCompany = !!companyId || isOnCompanyRoute;

  const [inputValue, setInputValue] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const isKeyboardNav = useRef(false);

  const browserGeo = useBrowserCoordinates(serverLat);
  const userLat = serverLat ?? browserGeo?.lat;
  const userLng = serverLng ?? browserGeo?.lng;

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

  // Work-mode is a tiny fixed-cardinality dimension matched client-side.
  // Read the active selection from the URL when not provided as a prop
  // (mirrors how `currentLocationSlugs` falls back to the `loc` param).
  const currentWorkMode: WorkMode[] = workModeProp ?? parseWorkModeParam(searchParams.get("wm"));
  const selectedWorkModes = useMemo(() => new Set<WorkMode>(currentWorkMode), [currentWorkMode]);

  // Filter context shared across typeahead boost queries. Each suggest*
  // call omits the dimension it's suggesting (same convention as the
  // browse-all modals) so users see counts under their *other* filters.
  const baseLocationIds = locationsProp?.length ? locationsProp.map((l) => l.id) : undefined;
  const baseOccupationIds = occupationsProp?.length ? occupationsProp.map((o) => o.id) : undefined;
  const baseSeniorityIds = senioritiesProp?.length ? senioritiesProp.map((s) => s.id) : undefined;
  const baseTechnologyIds = technologiesProp?.length ? technologiesProp.map((t) => t.id) : undefined;
  const baseLanguages = languagesProp?.length ? languagesProp : undefined;
  const baseKeywords = currentKeywords.length > 0 ? currentKeywords : undefined;

  const openSuggestions = useCallback(() => setIsOpen(true), []);
  const closeSuggestions = useCallback(() => setIsOpen(false), []);
  const resetActiveIndex = useCallback(() => setActiveIndex(-1), []);
  const {
    locationResults,
    companyResults,
    occupationResults,
    seniorityResults,
    technologyResults,
    clearResults,
    fetchSuggestions,
  } = useSearchBarTypeahead({
    lang,
    userLat,
    userLng,
    companyId,
    scopedToCompany,
    selectedLocationIds,
    selectedLocationSlugs,
    selectedOccupationIds,
    selectedSeniorityIds,
    selectedTechnologyIds,
    selectedWorkModes,
    baseKeywords,
    baseLocationIds,
    baseOccupationIds,
    baseSeniorityIds,
    baseTechnologyIds,
    baseLanguages,
    onOpen: openSuggestions,
    onClose: closeSuggestions,
    onResetActiveIndex: resetActiveIndex,
  });

  // Build flat list for keyboard navigation
  // "keyword" option first so user can search by title, then structured suggestions
  const trimmedInput = inputValue.trim();
  // Work-mode results are computed synchronously from a tiny static
  // alias map (no server round-trip). Issue #2983.
  const workModeResults: WorkMode[] = useMemo(
    () => matchWorkModes(trimmedInput, selectedWorkModes),
    [trimmedInput, selectedWorkModes],
  );
  // Show the "Request <query>" entry only when the user has a non-empty
  // query, no real company match has come back, and we're not scoped to
  // a single company page (where cross-company nav would be a trap).
  const showRequestItem =
    trimmedInput.length >= 2 && companyResults.length === 0 && !scopedToCompany;
  const allSuggestions: SuggestionItem[] = [
    ...(trimmedInput.length >= 2
      ? [{ kind: "keyword" as const, data: { text: trimmedInput } }]
      : []),
    ...occupationResults.map((s): SuggestionItem => ({ kind: "occupation", data: s })),
    ...seniorityResults.map((s): SuggestionItem => ({ kind: "seniority", data: s })),
    ...technologyResults.map((s): SuggestionItem => ({ kind: "technology", data: s })),
    ...workModeResults.map((value): SuggestionItem => ({ kind: "workMode", data: { value } })),
    ...locationResults.map((s): SuggestionItem => ({ kind: "location", data: s })),
    ...companyResults.map((s): SuggestionItem => ({ kind: "company", data: s })),
    ...(showRequestItem
      ? [{ kind: "request" as const, data: { query: trimmedInput } }]
      : []),
  ];

  const selectItem = useCallback(
    (item: SuggestionItem) => {
      if (item.kind === "keyword") {
        // User selected "Search for 'X' as title keyword"
        void submitFreeTextSearch();
        return;
      }
      if (item.kind === "request") {
        // Navigate to the company-request landing page (jobseek#2808)
        // with the raw query pre-filled. Encode the name so spaces and
        // any URL-significant characters survive the round-trip.
        router.push(
          lp(`/companies/request?name=${encodeURIComponent(item.data.query)}`),
        );
        setInputValue("");
        clearResults();
        setIsOpen(false);
        setActiveIndex(-1);
        return;
      }
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
      } else if (item.kind === "workMode") {
        // Issue #2983 — work-mode select. Mirrors the occupation/
        // seniority/technology dispatch flow: direct prop callback,
        // then live page action, then URL push fallback.
        const mode = item.data.value;
        if (onAddWorkMode) {
          onAddWorkMode(mode);
        } else {
          const pageActions = getPageActions();
          if (pageActions?.addWorkMode) {
            pageActions.addWorkMode(mode);
          } else {
            const p = new URLSearchParams(searchParams.toString());
            const existing = p.get("wm");
            const merged = existing
              ? Array.from(new Set([...existing.split(",").filter(Boolean), mode])).join(",")
              : mode;
            p.set("wm", merged);
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
        const wm = workModeProp ?? parseWorkModeParam(searchParams.get("wm"));
        const href = buildFilteredPath(
          lp(`/company/${item.data.slug}`),
          kws,
          locs,
          undefined,
          occs,
          sens,
          techs,
          wm,
        );
        router.push(href);
      }
      setInputValue("");
      clearResults();
      setIsOpen(false);
      setActiveIndex(-1);
      if (item.kind !== "company") {
        inputRef.current?.focus();
      }
    },
    [onAddLocation, onAddOccupation, onAddSeniority, onAddTechnology, onAddWorkMode, getPageActions, router, lp, searchParams, currentKeywords, currentLocationSlugs, keywordsProp, locationsProp, occupationsProp, senioritiesProp, technologiesProp, workModeProp, clearResults],
  );

  const submitFreeTextSearch = useCallback(() => {
    const input = inputValue.trim();
    if (!input) return;

    // Clear input immediately for instant feedback
    setInputValue("");
    clearResults();
    setIsOpen(false);
    setActiveIndex(-1);

    // Snapshot existing filters synchronously before async work
    const pageActions = getPageActions();
    const existingKw = keywordsProp ?? pageActions?.getKeywords() ?? currentKeywords;
    const existingLocs = locationsProp ?? pageActions?.getLocations() ?? [];
    const existingOccs = occupationsProp ?? pageActions?.getOccupations() ?? [];
    const existingSens = senioritiesProp ?? pageActions?.getSeniorities() ?? [];
    const existingTechs = technologiesProp ?? pageActions?.getTechnologies?.() ?? [];
    // Existing work-mode comes from prop (search/company pages own state)
    // or, when the bar is rendered standalone (header on a non-search
    // route), from the URL.
    const existingWm: WorkMode[] = workModeProp ?? parseWorkModeParam(searchParams.get("wm"));

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
        const wmSet = new Set(existingWm);
        const mergedWm = [...existingWm, ...(parsed.workMode ?? []).filter((m) => !wmSet.has(m))];

        router.push(buildFilteredPath(lp("/explore"), mergedKw, mergedLocs, undefined, mergedOccs, mergedSens, mergedTechs, mergedWm));
      })
      .catch(() => {
        // Fallback: treat raw input as a keyword and navigate
        const mergedKw = [...existingKw, input];
        router.push(buildFilteredPath(lp("/explore"), mergedKw, existingLocs, undefined, existingOccs, existingSens, existingTechs, existingWm));
      });
  }, [inputValue, lang, userLat, userLng, getPageActions, router, lp, searchParams, keywordsProp, locationsProp, occupationsProp, senioritiesProp, technologiesProp, workModeProp, currentKeywords, clearResults]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      if (!isOpen || allSuggestions.length === 0) return;
      e.preventDefault();
      isKeyboardNav.current = true;
      setActiveIndex((prev) =>
        prev < allSuggestions.length - 1 ? prev + 1 : prev,
      );
    } else if (e.key === "ArrowUp") {
      if (!isOpen || allSuggestions.length === 0) return;
      e.preventDefault();
      isKeyboardNav.current = true;
      setActiveIndex((prev) =>
        prev > 0 ? prev - 1 : prev,
      );
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (activeIndex >= 0 && activeIndex < allSuggestions.length) {
        selectItem(allSuggestions[activeIndex]);
      } else if (allSuggestions.length === 1 && allSuggestions[0].kind === "keyword") {
        // Auto-select when keyword is the only option
        selectItem(allSuggestions[0]);
      }
      // Otherwise no action — user must select from dropdown
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

  // Compute flat indices for each section (keyword → occupations → seniorities → technologies → workMode → locations → companies → request)
  let flatIdx = 0;
  const keywordIndex = flatIdx;
  flatIdx += trimmedInput.length >= 2 ? 1 : 0;
  const occStartIndex = flatIdx;
  flatIdx += occupationResults.length;
  const senStartIndex = flatIdx;
  flatIdx += seniorityResults.length;
  const techStartIndex = flatIdx;
  flatIdx += technologyResults.length;
  const wmStartIndex = flatIdx;
  flatIdx += workModeResults.length;
  const locStartIndex = flatIdx;
  flatIdx += locationResults.length;
  const companyStartIndex = flatIdx;
  flatIdx += companyResults.length;
  const requestIndex = flatIdx;

  function workModeLabel(value: WorkMode) {
    if (value === "onsite") {
      return t({
        id: "search.workMode.onsite",
        comment: "Work mode: onsite (in-office)",
        message: "On-site",
      });
    }
    if (value === "hybrid") {
      return t({
        id: "search.workMode.hybrid",
        comment: "Work mode: hybrid (mixed onsite/remote)",
        message: "Hybrid",
      });
    }
    return t({
      id: "search.workMode.remote",
      comment: "Work mode: remote (work-from-home)",
      message: "Remote",
    });
  }

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
          className="absolute left-0 top-full z-50 mt-1 w-full min-w-64 rounded-lg border border-border-soft bg-surface shadow-lg"
        >
        <ScrollFade className="max-h-[366px]" deps={[allSuggestions.length]}>
          {trimmedInput.length >= 2 && (
            <div
              id={`search-option-${keywordIndex}`}
              role="option"
              aria-selected={keywordIndex === activeIndex}
              data-suggestion
              onMouseDown={(e) => {
                e.preventDefault();
                selectItem({ kind: "keyword", data: { text: trimmedInput } });
              }}
              onMouseEnter={() => setActiveIndex(keywordIndex)}
              className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                keywordIndex === activeIndex ? "bg-primary/10" : "hover:bg-primary/5"
              }`}
            >
              <Search className="h-4 w-4 shrink-0 text-muted" />
              <span>
                {t({
                  id: "search.bar.keyword",
                  comment: "Option to search by title keyword in dropdown",
                  message: `Search for "${trimmedInput}" in job titles`,
                })}
              </span>
              <ArrowRight className="ml-auto h-3 w-3 text-muted" />
            </div>
          )}
          <SearchBarSuggestionSection
            items={occupationResults}
            header={t({
              id: "search.bar.roles",
              comment: "Section header for occupation suggestions in search bar",
              message: "Roles",
            })}
            startIndex={occStartIndex}
            activeIndex={activeIndex}
            hasDivider={trimmedInput.length >= 2}
            getKey={(s) => `occ-${s.id}`}
            renderIcon={() => <Briefcase size={14} className="shrink-0 text-muted" />}
            renderLabel={(s) => <span className="min-w-0 flex-1 font-medium">{s.name}</span>}
            onActiveIndex={setActiveIndex}
            onSelect={(s) => selectItem({ kind: "occupation", data: s })}
          />

          <SearchBarSuggestionSection
            items={seniorityResults}
            header={t({
              id: "search.bar.level",
              comment: "Section header for seniority suggestions in search bar",
              message: "Level",
            })}
            startIndex={senStartIndex}
            activeIndex={activeIndex}
            hasDivider={occupationResults.length > 0}
            getKey={(s) => `sen-${s.id}`}
            renderIcon={() => <BarChart3 size={14} className="shrink-0 text-muted" />}
            renderLabel={(s) => <span className="min-w-0 flex-1 font-medium">{s.name}</span>}
            onActiveIndex={setActiveIndex}
            onSelect={(s) => selectItem({ kind: "seniority", data: s })}
          />

          <SearchBarSuggestionSection
            items={technologyResults}
            header={t({
              id: "search.bar.technologies",
              comment: "Section header for technology suggestions in search bar",
              message: "Technologies",
            })}
            startIndex={techStartIndex}
            activeIndex={activeIndex}
            hasDivider={occupationResults.length > 0 || seniorityResults.length > 0}
            getKey={(s) => `tech-${s.id}`}
            renderIcon={() => <Code2 size={14} className="shrink-0 text-muted" />}
            renderLabel={(s) => <span className="min-w-0 flex-1 font-medium">{s.name}</span>}
            onActiveIndex={setActiveIndex}
            onSelect={(s) => selectItem({ kind: "technology", data: s })}
          />

          <SearchBarSuggestionSection
            items={workModeResults}
            header={t({
              id: "search.bar.workMode",
              comment: "Section header for work-mode (onsite/hybrid/remote) suggestions in search bar",
              message: "Work mode",
            })}
            startIndex={wmStartIndex}
            activeIndex={activeIndex}
            hasDivider={occupationResults.length > 0 || seniorityResults.length > 0 || technologyResults.length > 0}
            getKey={(value) => `wm-${value}`}
            getTestId={(value) => `search-bar-workmode-${value}`}
            renderIcon={() => <Home size={14} className="shrink-0 text-muted" />}
            renderLabel={(value) => <span className="min-w-0 flex-1 font-medium">{workModeLabel(value)}</span>}
            onActiveIndex={setActiveIndex}
            onSelect={(value) => selectItem({ kind: "workMode", data: { value } })}
          />

          <SearchBarSuggestionSection
            items={locationResults}
            header={t({
              id: "search.bar.locations",
              comment: "Section header for location suggestions in search bar",
              message: "Locations",
            })}
            startIndex={locStartIndex}
            activeIndex={activeIndex}
            hasDivider={occupationResults.length > 0 || seniorityResults.length > 0 || technologyResults.length > 0 || workModeResults.length > 0}
            getKey={(s) => `loc-${s.id}`}
            renderIcon={() => <MapPin size={14} className="shrink-0 text-muted" />}
            renderLabel={(s) => (
              <div className="min-w-0 flex-1">
                <span className="font-medium">{s.name}</span>
                {s.parentName && (
                  <span className="text-muted">, {s.parentName}</span>
                )}
              </div>
            )}
            onActiveIndex={setActiveIndex}
            onSelect={(s) => selectItem({ kind: "location", data: s })}
          />

          <SearchBarSuggestionSection
            items={companyResults}
            header={t({
              id: "search.bar.companies",
              comment: "Section header for company suggestions in search bar",
              message: "Companies",
            })}
            startIndex={companyStartIndex}
            activeIndex={activeIndex}
            hasDivider={occupationResults.length > 0 || seniorityResults.length > 0 || technologyResults.length > 0 || workModeResults.length > 0 || locationResults.length > 0}
            getKey={(c) => `co-${c.id}`}
            renderIcon={(c) => <CompanyIcon icon={c.icon} alt="" size={16} />}
            renderLabel={(c) => <span className="min-w-0 flex-1 font-medium">{c.name}</span>}
            renderTrailing={() => <ArrowRight size={12} className="shrink-0 text-muted" />}
            onActiveIndex={setActiveIndex}
            onSelect={(c) => selectItem({ kind: "company", data: c })}
          />

          {showRequestItem && (
            <div
              id={`search-option-${requestIndex}`}
              role="option"
              aria-selected={requestIndex === activeIndex}
              data-suggestion
              data-testid="search-bar-request-item"
              onMouseDown={(e) => {
                e.preventDefault();
                selectItem({ kind: "request", data: { query: trimmedInput } });
              }}
              onMouseEnter={() => setActiveIndex(requestIndex)}
              className={`flex cursor-pointer items-start gap-2 px-3 py-2 text-sm border-t border-border-soft ${
                requestIndex === activeIndex ? "bg-primary/10" : "hover:bg-primary/5"
              }`}
            >
              <Sparkles size={14} className="mt-0.5 shrink-0 text-primary" />
              <div className="min-w-0 flex-1">
                <div className="font-medium">
                  {t({
                    id: "search.bar.request",
                    comment: "Synthetic dropdown row that lets the user request a company that's not in the catalog",
                    message: `Request "${trimmedInput}"`,
                  })}
                </div>
                <div className="text-xs text-muted">
                  {t({
                    id: "search.bar.request.subtext",
                    comment: "Secondary line under the Request <query> dropdown row",
                    message: "We'll start tracking it",
                  })}
                </div>
              </div>
              <ArrowRight size={12} className="mt-1 shrink-0 text-muted" />
            </div>
          )}
        </ScrollFade>
        </div>
      )}
    </div>
  );
}
