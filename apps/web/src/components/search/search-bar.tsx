"use client";

import { useState, useRef, useEffect, useCallback, useId, useMemo } from "react";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Search,
  MapPin,
  ArrowRight,
  Briefcase,
  BarChart3,
  Code2,
  Loader2,
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
import { useSearchBarQueryIntent } from "@/components/search/use-search-bar-query-intent";
import type { QueryCandidate, QueryIntentProposal } from "@/lib/search/query-intent";
import { correctQueryProposal } from "@/lib/search/query-proposal-edits";

type SuggestionItem =
  | { kind: "keyword"; data: { text: string } }
  | { kind: "proposal"; data: QueryIntentProposal }
  | { kind: "correction"; data: { proposal: QueryIntentProposal; termIndex: number; candidate: QueryCandidate | null } }
  | { kind: "occupation"; data: TaxonomySuggestion & { sourceTerm?: string } }
  | { kind: "seniority"; data: TaxonomySuggestion & { sourceTerm?: string } }
  | { kind: "technology"; data: TaxonomySuggestion & { sourceTerm?: string } }
  | { kind: "workMode"; data: { value: WorkMode } }
  | { kind: "location"; data: LocationSuggestion & { sourceTerm?: string } }
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
  employmentTypes?: string[];
  languages?: string[];
  companyId?: string;
  userLat?: number;
  userLng?: number;
  className?: string;
  placeholder?: string;
  /** Stable accessible name for the search scope. */
  accessibleLabel?: string;
}

export function SearchBar({
  onAddLocation,
  onAddOccupation,
  onAddSeniority,
  onAddTechnology,
  onAddWorkMode,
  onSubmitSearch,
  locale: localeProp,
  keywords: keywordsProp,
  locations: locationsProp,
  occupations: occupationsProp,
  seniorities: senioritiesProp,
  technologies: technologiesProp,
  workMode: workModeProp,
  employmentTypes: employmentTypesProp,
  languages: languagesProp,
  companyId,
  userLat: serverLat,
  userLng: serverLng,
  className,
  placeholder: placeholderProp,
  accessibleLabel: accessibleLabelProp,
}: SearchBarProps) {
  const { t } = useLingui();
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const lp = useLocalePath();
  const { getPageActions } = useSearchStateStore();
  const reactivePageActions = usePageActions();

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
  const activeIndexRef = useRef(-1);
  activeIndexRef.current = activeIndex;
  const [showProposal, setShowProposal] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const isKeyboardNav = useRef(false);
  const submittingRef = useRef(false);
  const submissionVersion = useRef(0);
  const inputEditVersion = useRef(0);
  const listboxId = useId();

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
  const currentWorkMode: WorkMode[] = workModeProp ?? reactivePageActions?.getWorkMode?.() ?? parseWorkModeParam(searchParams.get("wm"));
  const currentEmploymentTypes = employmentTypesProp ?? reactivePageActions?.getEmploymentTypes?.() ??
    (searchParams.get("etype") ?? "").split(",").filter(Boolean);
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
    sourceQuery,
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
  const atomicSuggestionsRef = useRef({ sourceQuery, locationResults, occupationResults,
    seniorityResults, technologyResults });
  atomicSuggestionsRef.current = { sourceQuery, locationResults, occupationResults,
    seniorityResults, technologyResults };
  const isUnresolvedAtomic = useCallback((query: string) => {
    const state = atomicSuggestionsRef.current;
    if (state.sourceQuery.toLowerCase() !== query.toLowerCase()) return false;
    if (matchWorkModes(query, new Set()).length) return false;
    const normalize = (value: string) => value.toLowerCase().normalize("NFKD")
      .replace(/[^\p{L}\p{N}]+/gu, "");
    const desired = normalize(query);
    const exact = [state.locationResults, state.occupationResults,
      state.seniorityResults, state.technologyResults].flat()
      .filter((item) => [item.name, item.slug, "matchedName" in item ? item.matchedName : undefined]
        .some((value) => value && normalize(value) === desired));
    return exact.length !== 1;
  }, []);
  const {
    proposal,
    pending: proposalPending,
    onInput: onQueryInput,
    requestNow: requestQueryIntent,
    replaceProposal,
    clear: clearQueryIntent,
  } = useSearchBarQueryIntent(lang, JSON.stringify([
    companyId, scopedToCompany, currentKeywords,
    currentLocationSlugs,
    [...selectedOccupationIds], [...selectedSeniorityIds], [...selectedTechnologyIds],
    [...selectedWorkModes],
    currentEmploymentTypes,
  ]), () => {
    // A late proposal inserts rows ahead of taxonomy choices. Keep the
    // option under an in-progress keyboard selection stable.
    if (activeIndexRef.current === -1) {
      setShowProposal(true);
      setIsOpen(true);
    }
  }, isUnresolvedAtomic);

  // Build flat list for keyboard navigation
  // "keyword" option first so user can search by title, then structured suggestions
  const trimmedInput = inputValue.trim();
  const activeTerm = trimmedInput.split(/[\s,\/|]+/).filter(Boolean).at(-1) ?? trimmedInput;
  const visibleProposal = showProposal && proposal?.query === trimmedInput ? proposal : null;
  const ambiguousSpans = visibleProposal?.terms.filter((term) => term.status === "ambiguous") ?? [];
  const ambiguousWords = new Set(ambiguousSpans.flatMap((term) => term.span.text.split(/\s+/).map((word) => word.toLowerCase())));
  const corrections: SuggestionItem[] = visibleProposal
    ? visibleProposal.terms.flatMap((term, termIndex) => {
        if (term.status !== "ambiguous" && term.status !== "approximate") return [];
        const normalized = term.span.text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
        const alternatives = term.status === "ambiguous"
          ? term.alternatives.filter((item) => [item.name, item.matchedName].some((value) =>
              value?.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "") === normalized)).slice(0, 3)
          : term.alternatives.slice(0, 2);
        return [
          ...alternatives.map((candidate): SuggestionItem => ({
            kind: "correction", data: { proposal: visibleProposal, termIndex, candidate },
          })),
          ...(term.status === "approximate" ? [{
            kind: "correction" as const,
            data: { proposal: visibleProposal, termIndex, candidate: null },
          }] : []),
        ];
      })
    : [];
  const correctionIds = new Map<string, Set<number>>();
  for (const item of corrections) {
    if (item.kind !== "correction" || !item.data.candidate) continue;
    const category = item.data.proposal.terms[item.data.termIndex]?.span.category;
    if (!category) continue;
    if (!correctionIds.has(category)) correctionIds.set(category, new Set());
    correctionIds.get(category)!.add(item.data.candidate.id);
  }
  const displayedOccupations = occupationResults.filter((item) => !correctionIds.get("occupation")?.has(item.id));
  const displayedSeniorities = seniorityResults.filter((item) => !correctionIds.get("seniority")?.has(item.id));
  const displayedTechnologies = technologyResults.filter((item) => !correctionIds.get("technology")?.has(item.id));
  const displayedLocations = locationResults.filter((item) => !correctionIds.get("location")?.has(item.id));
  // Work-mode results are computed synchronously from a tiny static
  // alias map (no server round-trip). Issue #2983.
  const workModeResults: WorkMode[] = useMemo(
    () => matchWorkModes(activeTerm, selectedWorkModes),
    [activeTerm, selectedWorkModes],
  );
  // Show the "Request <query>" entry only when the user has a non-empty
  // query, no real company match has come back, and we're not scoped to
  // a single company page (where cross-company nav would be a trap).
  const showRequestItem =
    trimmedInput.length >= 2 && companyResults.length === 0 && !scopedToCompany &&
    !(visibleProposal?.intent === "jobSearch" &&
      visibleProposal.terms.some((term) => term.span.category !== "discard"));
  const allSuggestions: SuggestionItem[] = [
    ...(trimmedInput.length >= 2
      ? [{ kind: "keyword" as const, data: { text: trimmedInput } }]
      : []),
    ...(visibleProposal ? [{ kind: "proposal" as const, data: visibleProposal }] : []),
    ...corrections,
    ...displayedOccupations.map((s): SuggestionItem => ({ kind: "occupation", data: s })),
    ...displayedSeniorities.map((s): SuggestionItem => ({ kind: "seniority", data: s })),
    ...displayedTechnologies.map((s): SuggestionItem => ({ kind: "technology", data: s })),
    ...workModeResults.map((value): SuggestionItem => ({ kind: "workMode", data: { value } })),
    ...displayedLocations.map((s): SuggestionItem => ({ kind: "location", data: s })),
    ...companyResults.map((s): SuggestionItem => ({ kind: "company", data: s })),
    ...(showRequestItem
      ? [{ kind: "request" as const, data: { query: trimmedInput } }]
      : []),
  ];

  const submitProposal = useCallback((draft: QueryIntentProposal) => {
    const pageActions = getPageActions();
    const existingKw = keywordsProp ?? pageActions?.getKeywords() ?? currentKeywords;
    const existingLocs = locationsProp ?? pageActions?.getLocations() ?? [];
    const existingOccs = occupationsProp ?? pageActions?.getOccupations() ?? [];
    const existingSens = senioritiesProp ?? pageActions?.getSeniorities() ?? [];
    const existingTechs = technologiesProp ?? pageActions?.getTechnologies?.() ?? [];
    const existingWm = workModeProp ?? pageActions?.getWorkMode?.() ?? parseWorkModeParam(searchParams.get("wm"));
    const mergeBy = <T,>(existing: T[], added: readonly T[], key: (value: T) => string | number): T[] => {
      const seen = new Set(existing.map(key));
      return [...existing, ...added.filter((value) => {
        const id = key(value);
        if (seen.has(id)) return false;
        seen.add(id);
        return true;
      })];
    };
    const keywords = mergeBy(existingKw, draft.keywords, (value) => value.toLowerCase());
    const locations = mergeBy(existingLocs, draft.locations.map((value): SelectedLocation => ({
      id: value.id, slug: value.slug, name: value.name,
      type: (value.type ?? "city") as SelectedLocation["type"], parentName: value.parentName ?? null,
    })), (value) => value.id);
    const occupations = mergeBy(existingOccs, draft.occupations, (value) => value.id);
    const seniorities = mergeBy(existingSens, draft.seniorities, (value) => value.id);
    const technologies = mergeBy(existingTechs, draft.technologies, (value) => value.id);
    const workMode = mergeBy(existingWm, draft.workMode, (value) => value);
    const employmentTypes = mergeBy(
      employmentTypesProp ?? pageActions?.getEmploymentTypes?.() ?? currentEmploymentTypes,
      draft.employmentTypes,
      (value) => value,
    );
    const extra: Record<string, string> = { qmode: "literal" };
    if (employmentTypes.length) extra.etype = employmentTypes.join(",");
    for (const key of ["sal", "salcur", "exp", "lang"]) {
      const value = searchParams.get(key);
      if (value) extra[key] = value;
    }
    const target = scopedToCompany ? pathname : lp("/explore");
    router.push(buildFilteredPath(target, keywords, locations, extra,
      occupations, seniorities, technologies, workMode));
    setInputValue("");
    clearResults();
    clearQueryIntent();
    setIsOpen(false);
    setActiveIndex(-1);
  }, [getPageActions, keywordsProp, locationsProp, occupationsProp, senioritiesProp, technologiesProp,
    workModeProp, employmentTypesProp, currentEmploymentTypes, currentKeywords, searchParams, scopedToCompany, pathname, lp, router, clearResults, clearQueryIntent]);

  const selectItem = useCallback(
    (item: SuggestionItem) => {
      inputEditVersion.current += 1;
      submissionVersion.current += 1;
      submittingRef.current = false;
      if (item.kind === "proposal") {
        submitProposal(item.data);
        return;
      }
      if (item.kind === "correction") {
        replaceProposal(correctQueryProposal(item.data.proposal, item.data.termIndex, item.data.candidate));
        setActiveIndex(-1);
        inputRef.current?.focus();
        return;
      }
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
      const sourceTerm = item.kind === "workMode" ? activeTerm
        : "sourceTerm" in item.data ? item.data.sourceTerm : sourceQuery;
      let remaining = "";
      if (item.kind !== "company" && sourceTerm) {
        const lowerInput = inputValue.toLowerCase();
        const position = lowerInput.lastIndexOf(sourceTerm.toLowerCase());
        if (position >= 0) {
          remaining = `${inputValue.slice(0, position)} ${inputValue.slice(position + sourceTerm.length)}`
            .trim().replace(/\s+/g, " ");
        }
      }
      setInputValue(remaining);
      clearResults();
      onQueryInput(remaining);
      if (remaining.length >= 2) fetchSuggestions(remaining);
      else setIsOpen(false);
      setActiveIndex(-1);
      if (item.kind !== "company") {
        inputRef.current?.focus();
      }
    },
    [onAddLocation, onAddOccupation, onAddSeniority, onAddTechnology, onAddWorkMode, getPageActions, router, lp, searchParams, currentKeywords, currentLocationSlugs, keywordsProp, locationsProp, occupationsProp, senioritiesProp, technologiesProp, workModeProp, clearResults, submitProposal, replaceProposal, sourceQuery, activeTerm, inputValue, onQueryInput, fetchSuggestions],
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

        if (onSubmitSearch) {
          onSubmitSearch(mergedKw, mergedLocs, mergedOccs, mergedSens, mergedTechs, mergedWm);
        } else if (pageActions) {
          pageActions.submitSearch(mergedKw, mergedLocs, mergedOccs, mergedSens, mergedTechs, mergedWm);
        } else {
          router.push(buildFilteredPath(lp("/explore"), mergedKw, mergedLocs, undefined, mergedOccs, mergedSens, mergedTechs, mergedWm));
        }
      })
      .catch(() => {
        // Fallback: treat raw input as a keyword and navigate
        const mergedKw = [...existingKw, input];
        if (onSubmitSearch) {
          onSubmitSearch(mergedKw, existingLocs, existingOccs, existingSens, existingTechs, existingWm);
        } else if (pageActions) {
          pageActions.submitSearch(mergedKw, existingLocs, existingOccs, existingSens, existingTechs, existingWm);
        } else {
          router.push(buildFilteredPath(lp("/explore"), mergedKw, existingLocs, undefined, existingOccs, existingSens, existingTechs, existingWm));
        }
      });
  }, [inputValue, lang, userLat, userLng, getPageActions, onSubmitSearch, router, lp, searchParams, keywordsProp, locationsProp, occupationsProp, senioritiesProp, technologiesProp, workModeProp, currentKeywords, clearResults]);

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
      } else if (trimmedInput.length >= 2) {
        if (submittingRef.current) return;
        submittingRef.current = true;
        const submitId = ++submissionVersion.current;
        const entered = trimmedInput;
        const editVersion = inputEditVersion.current;
        void requestQueryIntent(entered).then((draft) => {
          if (submissionVersion.current !== submitId || inputEditVersion.current !== editVersion ||
            inputRef.current?.value.trim() !== entered) return;
          if (draft) submitProposal(draft);
          else submitFreeTextSearch();
        }).finally(() => { if (submissionVersion.current === submitId) submittingRef.current = false; });
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

  const placeholder = placeholderProp ?? reactivePageActions?.placeholder ?? t({
    id: "search.bar.placeholder",
    comment: "Placeholder for the main search bar",
    message: "Search...",
  });
  const accessibleLabel =
    accessibleLabelProp ??
    reactivePageActions?.accessibleLabel ??
    t({
      id: "search.bar.label",
      comment: "Accessible label for the main job search input",
      message: "Search jobs",
    });
  const listboxVisible = isOpen && allSuggestions.length > 0;
  const optionIdPrefix = `${listboxId}-option`;

  // Compute flat indices for each section (keyword → occupations → seniorities → technologies → workMode → locations → companies → request)
  let flatIdx = 0;
  const keywordIndex = flatIdx;
  flatIdx += trimmedInput.length >= 2 ? 1 : 0;
  const proposalIndex = flatIdx;
  flatIdx += visibleProposal ? 1 : 0;
  const correctionStartIndex = flatIdx;
  flatIdx += corrections.length;
  const occStartIndex = flatIdx;
  flatIdx += displayedOccupations.length;
  const senStartIndex = flatIdx;
  flatIdx += displayedSeniorities.length;
  const techStartIndex = flatIdx;
  flatIdx += displayedTechnologies.length;
  const wmStartIndex = flatIdx;
  flatIdx += workModeResults.length;
  const locStartIndex = flatIdx;
  flatIdx += displayedLocations.length;
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

  function locationTypeLabel(type: string) {
    if (type === "city") return t({ id: "location.type.city", message: "City" });
    if (type === "country") return t({ id: "location.type.country", message: "Country" });
    return t({ id: "location.type.region", message: "Region" });
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
            inputEditVersion.current += 1;
            submissionVersion.current += 1;
            submittingRef.current = false;
            setInputValue(e.target.value);
            setShowProposal(false);
            onQueryInput(e.target.value);
            fetchSuggestions(e.target.value);
          }}
          onFocus={() => {
            if (allSuggestions.length > 0) setIsOpen(true);
          }}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          className="w-full bg-transparent text-sm outline-none placeholder:text-muted"
          role="combobox"
          aria-label={accessibleLabel}
          aria-expanded={listboxVisible}
          aria-autocomplete="list"
          aria-controls={listboxVisible ? listboxId : undefined}
          aria-activedescendant={
            listboxVisible && activeIndex >= 0
              ? `${optionIdPrefix}-${activeIndex}`
              : undefined
          }
        />
        {proposalPending && (
          <Loader2 size={14} className="shrink-0 animate-spin text-muted" aria-hidden="true" />
        )}
      </div>

      {listboxVisible && (
        <div
          id={listboxId}
          ref={listRef}
          role="listbox"
          className="absolute left-0 top-full z-50 mt-1 w-full min-w-64 rounded-lg border border-border-soft bg-surface shadow-lg"
        >
        <ScrollFade className="max-h-[366px]" deps={[allSuggestions.length]}>
          {trimmedInput.length >= 2 && (
            <div
              id={`${optionIdPrefix}-${keywordIndex}`}
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
          {visibleProposal && (
            <div
              id={`${optionIdPrefix}-${proposalIndex}`}
              role="option"
              aria-selected={proposalIndex === activeIndex}
              data-suggestion
              data-testid="search-bar-query-proposal"
              onMouseDown={(e) => { e.preventDefault(); submitProposal(visibleProposal); }}
              onMouseEnter={() => setActiveIndex(proposalIndex)}
              className={`flex cursor-pointer items-start gap-2 border-t border-border-soft px-3 py-2 text-sm ${
                proposalIndex === activeIndex ? "bg-primary/10" : "hover:bg-primary/5"
              }`}
            >
              <div className="min-w-0 flex-1">
                <div className="font-medium">{visibleProposal.intent === "other"
                  ? t({ id: "search.bar.searchAsEntered", message: "Search these words as entered" })
                  : t({
                    id: "search.bar.applyFilters",
                    comment: "Single proposed complete filter setup for a multi-term job search",
                    message: "Search with suggested filters",
                  })}</div>
                <div className="line-clamp-2 text-xs text-muted">
                  {[
                    ...visibleProposal.occupations.map((value) => value.name),
                    ...visibleProposal.seniorities.map((value) => value.name),
                    ...visibleProposal.technologies.map((value) => value.name),
                    ...visibleProposal.locations.map((value) => value.name),
                    ...visibleProposal.workMode,
                    ...visibleProposal.employmentTypes,
                    ...visibleProposal.keywords.filter((word) => !ambiguousWords.has(word.toLowerCase())),
                    ...ambiguousSpans.map((term) => term.span.text),
                  ].join(" · ") || trimmedInput}
                </div>
                {visibleProposal.terms.some((term) => term.status === "ambiguous" || term.status === "approximate") && (
                  <div className="truncate text-xs text-primary">{t({
                    id: "search.bar.reviewSuggestedFilters",
                    comment: "Prompt to review uncertain city or occupation matches in the proposed search filters",
                    message: "Review uncertain matches below",
                  })}</div>
                )}
              </div>
              <ArrowRight size={12} className="mt-1 shrink-0 text-muted" aria-hidden="true" />
            </div>
          )}
          {corrections.map((item, index) => {
            if (item.kind !== "correction") return null;
            const term = item.data.proposal.terms[item.data.termIndex];
            const candidate = item.data.candidate;
            const rowIndex = correctionStartIndex + index;
            return (
              <div
                key={`${term.span.id}-${candidate?.id ?? "keyword"}`}
                id={`${optionIdPrefix}-${rowIndex}`}
                role="option"
                aria-selected={rowIndex === activeIndex}
                data-suggestion
                data-testid="search-bar-query-correction"
                onMouseDown={(e) => { e.preventDefault(); selectItem(item); }}
                onMouseEnter={() => setActiveIndex(rowIndex)}
                className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                  rowIndex === activeIndex ? "bg-primary/10" : "hover:bg-primary/5"
                }`}
              >
                {candidate ? (term.span.category === "location"
                  ? <MapPin size={14} className="shrink-0 text-muted" aria-hidden="true" />
                  : term.span.category === "occupation"
                    ? <Briefcase size={14} className="shrink-0 text-muted" aria-hidden="true" />
                    : term.span.category === "seniority"
                      ? <BarChart3 size={14} className="shrink-0 text-muted" aria-hidden="true" />
                      : <Code2 size={14} className="shrink-0 text-muted" aria-hidden="true" />)
                  : <Search size={14} className="shrink-0 text-muted" aria-hidden="true" />}
                <span className="min-w-0 flex-1 truncate">
                  {candidate ? `${candidate.name}${candidate.parentName ? `, ${candidate.parentName}` : ""}`
                    : t({ id: "search.bar.keepAsKeywords", message: `Keep "${term.span.text}" as keywords` })}
                </span>
                {term.span.category === "location" && candidate?.type &&
                  <span className="shrink-0 text-xs text-muted">{locationTypeLabel(candidate.type)}</span>}
              </div>
            );
          })}
          <SearchBarSuggestionSection
            items={displayedOccupations}
            header={t({
              id: "search.bar.roles",
              comment: "Section header for occupation suggestions in search bar",
              message: "Roles",
            })}
            optionIdPrefix={optionIdPrefix}
            startIndex={occStartIndex}
            activeIndex={activeIndex}
            hasDivider={trimmedInput.length >= 2 || !!visibleProposal || corrections.length > 0}
            getKey={(s) => `occ-${s.id}`}
            renderIcon={() => <Briefcase size={14} className="shrink-0 text-muted" />}
            renderLabel={(s) => <span className="min-w-0 flex-1 font-medium">{s.name}</span>}
            renderTrailing={(s) => s.sourceTerm && s.sourceTerm.toLowerCase() !== s.name.toLowerCase()
              ? <span className="max-w-20 truncate text-xs text-muted">{s.sourceTerm}</span> : null}
            onActiveIndex={setActiveIndex}
            onSelect={(s) => selectItem({ kind: "occupation", data: s })}
          />

          <SearchBarSuggestionSection
            items={displayedSeniorities}
            header={t({
              id: "search.bar.level",
              comment: "Section header for seniority suggestions in search bar",
              message: "Level",
            })}
            optionIdPrefix={optionIdPrefix}
            startIndex={senStartIndex}
            activeIndex={activeIndex}
            hasDivider={displayedOccupations.length > 0}
            getKey={(s) => `sen-${s.id}`}
            renderIcon={() => <BarChart3 size={14} className="shrink-0 text-muted" />}
            renderLabel={(s) => <span className="min-w-0 flex-1 font-medium">{s.name}</span>}
            renderTrailing={(s) => s.sourceTerm && s.sourceTerm.toLowerCase() !== s.name.toLowerCase()
              ? <span className="max-w-20 truncate text-xs text-muted">{s.sourceTerm}</span> : null}
            onActiveIndex={setActiveIndex}
            onSelect={(s) => selectItem({ kind: "seniority", data: s })}
          />

          <SearchBarSuggestionSection
            items={displayedTechnologies}
            header={t({
              id: "search.bar.technologies",
              comment: "Section header for technology suggestions in search bar",
              message: "Technologies",
            })}
            optionIdPrefix={optionIdPrefix}
            startIndex={techStartIndex}
            activeIndex={activeIndex}
            hasDivider={displayedOccupations.length > 0 || displayedSeniorities.length > 0}
            getKey={(s) => `tech-${s.id}`}
            renderIcon={() => <Code2 size={14} className="shrink-0 text-muted" />}
            renderLabel={(s) => <span className="min-w-0 flex-1 font-medium">{s.name}</span>}
            renderTrailing={(s) => s.sourceTerm && s.sourceTerm.toLowerCase() !== s.name.toLowerCase()
              ? <span className="max-w-20 truncate text-xs text-muted">{s.sourceTerm}</span> : null}
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
            optionIdPrefix={optionIdPrefix}
            startIndex={wmStartIndex}
            activeIndex={activeIndex}
            hasDivider={displayedOccupations.length > 0 || displayedSeniorities.length > 0 || displayedTechnologies.length > 0}
            getKey={(value) => `wm-${value}`}
            getTestId={(value) => `search-bar-workmode-${value}`}
            renderIcon={() => <Home size={14} className="shrink-0 text-muted" />}
            renderLabel={(value) => <span className="min-w-0 flex-1 font-medium">{workModeLabel(value)}</span>}
            onActiveIndex={setActiveIndex}
            onSelect={(value) => selectItem({ kind: "workMode", data: { value } })}
          />

          <SearchBarSuggestionSection
            items={displayedLocations}
            header={t({
              id: "search.bar.locations",
              comment: "Section header for location suggestions in search bar",
              message: "Locations",
            })}
            optionIdPrefix={optionIdPrefix}
            startIndex={locStartIndex}
            activeIndex={activeIndex}
            hasDivider={displayedOccupations.length > 0 || displayedSeniorities.length > 0 || displayedTechnologies.length > 0 || workModeResults.length > 0}
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
            renderTrailing={(s) => s.sourceTerm && s.sourceTerm.toLowerCase() !== s.name.toLowerCase()
              ? <span className="max-w-20 truncate text-xs text-muted">{s.sourceTerm}</span> : null}
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
            optionIdPrefix={optionIdPrefix}
            startIndex={companyStartIndex}
            activeIndex={activeIndex}
            hasDivider={displayedOccupations.length > 0 || displayedSeniorities.length > 0 || displayedTechnologies.length > 0 || workModeResults.length > 0 || displayedLocations.length > 0}
            getKey={(c) => `co-${c.id}`}
            renderIcon={(c) => <CompanyIcon icon={c.icon} alt="" size={16} />}
            renderLabel={(c) => <span className="min-w-0 flex-1 font-medium">{c.name}</span>}
            renderTrailing={() => <ArrowRight size={12} className="shrink-0 text-muted" />}
            onActiveIndex={setActiveIndex}
            onSelect={(c) => selectItem({ kind: "company", data: c })}
          />

          {showRequestItem && (
            <div
              id={`${optionIdPrefix}-${requestIndex}`}
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
