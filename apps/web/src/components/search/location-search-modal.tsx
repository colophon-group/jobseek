"use client";

import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Search, Loader2, Globe } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import {
  getGlobalLocationsPage,
  searchGlobalLocations,
} from "@/lib/actions/locations";
import type {
  GlobalLocationGroup,
  GlobalMacroRegion,
  GlobalLocationSearchHit,
} from "@/lib/actions/locations";
import { LOCATION_PAGE_SIZE } from "@/lib/search/location-paging";
import {
  getCachedLocationsFirstPage,
  getCachedLocationsFirstPageSync,
  prefetchLocationsFirstPage,
} from "@/lib/search/location-prefetch";
import { countryIso } from "@/lib/country-flags";
import { CountryFlag } from "@/components/country-flag";
import { findBestGuess } from "./best-guess";
import { ScrollFade } from "@/components/ui/scroll-fade";
import { useDisabledByAncestor, pruneRedundantDescendants } from "./use-disabled-by-ancestor";
import { DisabledFilterPill } from "./disabled-filter-pill";

/** Show region sub-headers when a country has more cities than this. */
const REGION_THRESHOLD = 8;

/** Debounce window for the server-side search input fetch (ms). */
const SEARCH_DEBOUNCE_MS = 180;

type SelectedLocation = { id: number; slug: string; name: string; type: string; parentName?: string | null };

interface LocationSearchModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  locale: string;
  selected: SelectedLocation[];
  onToggle: (loc: SelectedLocation) => void;
  filters?: { companyId?: string; keywords?: string[]; occupationIds?: number[]; seniorityIds?: number[]; technologyIds?: number[]; languages?: string[] };
}

export function LocationSearchModal({
  open,
  onOpenChange,
  locale,
  selected,
  onToggle,
  filters,
}: LocationSearchModalProps) {
  const { t } = useLingui();
  // Paged country list (#2982): instead of loading the full ~150-country
  // tree on every modal-open, we fetch in slices of LOCATION_PAGE_SIZE
  // and append on scroll-near-bottom. The first page also carries the
  // bounded `macros[]` cluster.
  const [pages, setPages] = useState<GlobalLocationGroup[]>([]);
  const [macros, setMacros] = useState<GlobalMacroRegion[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(0);
  const [totalCountries, setTotalCountries] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  // Server-side search hits (separate code path from the in-memory filter
  // over `pages`). Surfaces long-tail cities that aren't in the loaded
  // pages — see `searchGlobalLocations`.
  const [searchHits, setSearchHits] = useState<GlobalLocationSearchHit[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [warning, setWarning] = useState("");
  const warningTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Scroll container — observed by both ScrollFade (gradient overlay) and
  // the bottom IntersectionObserver that triggers loadMore.
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const selectedIds = useMemo(() => new Set(selected.map((s) => s.id)), [selected]);

  // Build parent map from the loaded country pages. Partial coverage is
  // fine — `useDisabledByAncestor` walks parents; an unknown id (not yet
  // loaded) just doesn't get disabled until its page lands. Macros are
  // NOT in the parent chain — they're consulted via the macroMembers
  // side-channel.
  const parentMap = useMemo(() => {
    const map = new Map<number, number | null>();
    for (const country of pages) {
      map.set(country.countryId, null);
      for (const region of country.regions) {
        if (region.regionId > 0) {
          map.set(region.regionId, country.countryId);
        }
        for (const loc of region.locations) {
          // Cities parent to region if present, else direct to country.
          map.set(loc.id, region.regionId > 0 ? region.regionId : country.countryId);
        }
      }
    }
    return map;
  }, [pages]);

  const macroMembersMap = useMemo(() => {
    const map = new Map<number, number[]>();
    for (const macro of macros) {
      map.set(macro.id, macro.memberCountryIds ?? []);
    }
    return map;
  }, [macros]);

  const { isDisabled, disabledByAncestor } = useDisabledByAncestor({
    selectedIds,
    parents: parentMap,
    macroMembers: macroMembersMap,
  });

  // Lookup table id -> localized name, used to render the
  // "Included in <ancestor>" tooltip without re-walking the response.
  const nameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const macro of macros) {
      map.set(macro.id, macro.name);
    }
    for (const country of pages) {
      map.set(country.countryId, country.countryName);
      for (const region of country.regions) {
        if (region.regionId > 0) map.set(region.regionId, region.regionName);
        for (const loc of region.locations) map.set(loc.id, loc.name);
      }
    }
    return map;
  }, [pages, macros]);

  const ancestorNameOf = useCallback((id: number): string => {
    const ancId = disabledByAncestor(id);
    if (ancId == null) return "";
    return nameById.get(ancId) ?? "";
  }, [disabledByAncestor, nameById]);

  // Wraps the caller's onToggle. After the parent commits, drop any
  // already-selected descendants that have just become redundant — keeps
  // the filter chip strip clean.
  const handleToggle = useCallback((loc: SelectedLocation) => {
    const wasSelected = selectedIds.has(loc.id);
    onToggle(loc);
    if (wasSelected) return;
    // Find descendants in the current selection that this commit
    // subsumes, and toggle them off. The `pruneRedundantDescendants`
    // helper computes the keep-set; anything not in the keep-set must
    // be deselected by re-emitting onToggle for it.
    const nextSelected = [...selected, loc];
    const kept = pruneRedundantDescendants(nextSelected, parentMap, macroMembersMap);
    const keptIds = new Set(kept.map((s) => s.id));
    for (const s of selected) {
      if (!keptIds.has(s.id)) {
        // descendant of `loc` (or another selection) — drop it
        onToggle(s);
      }
    }
  }, [onToggle, parentMap, macroMembersMap, selected, selectedIds]);

  const filtersKey = filters ? JSON.stringify(filters) : "";
  const prevFiltersKeyRef = useRef(filtersKey);
  // Track the most recent in-flight first-page fetch so a stale response
  // (filter changed mid-flight) doesn't clobber the fresh state.
  const fetchSeqRef = useRef(0);

  // First page on open / on filter change. Always cursor=0; resets the
  // accumulated pages list so the second open of the modal shows a fresh
  // first page rather than the tail of the previous session.
  //
  // The close-side companion below (`reset on close`) handles the actual
  // accumulator-on-close-reopen contract — fixes #3000 where the
  // `firstOpen` guard returned false after a partial-scroll close because
  // `pages.length` was nonzero. With the close-effect clearing state, the
  // re-open path naturally goes through the firstOpen branch.
  //
  // #3031: before firing the server action, consult the client prefetch
  // cache (`location-prefetch.ts`). On a warm reopen the cache holds the
  // resolved page and we seed React state from it synchronously in the
  // SAME commit as the open transition, so the modal mounts with content
  // already populated (no spinner, no server round-trip). On a cold open
  // after a hover/expand prefetch the cache holds an in-flight promise
  // and we wait on it instead of starting a duplicate fetch.
  useEffect(() => {
    if (!open) return;
    const filtersChanged = filtersKey !== prevFiltersKeyRef.current;
    const firstOpen = pages.length === 0 && nextCursor === 0;
    if (!filtersChanged && !firstOpen) return;
    prevFiltersKeyRef.current = filtersKey;
    const seq = ++fetchSeqRef.current;

    // Fast path: cache has resolved value — seed state in a single
    // commit, no spinner.
    const sync = getCachedLocationsFirstPageSync(locale, filters);
    if (sync) {
      setMacros(sync.macros);
      setPages(sync.countries);
      setNextCursor(sync.nextCursor);
      setTotalCountries(sync.totalCountries);
      setLoading(false);
      return;
    }

    // Slow path: cache miss or inflight — show spinner, await the (possibly
    // already-started) prefetch.
    setPages([]);
    setMacros([]);
    setNextCursor(0);
    setTotalCountries(0);
    setLoading(true);
    const inflight =
      getCachedLocationsFirstPage(locale, filters)
      ?? prefetchLocationsFirstPage(locale, filters, getGlobalLocationsPage);
    inflight
      .then((page) => {
        if (fetchSeqRef.current !== seq) return; // stale — drop
        setMacros(page.macros);
        setPages(page.countries);
        setNextCursor(page.nextCursor);
        setTotalCountries(page.totalCountries);
      })
      .finally(() => {
        if (fetchSeqRef.current !== seq) return;
        setLoading(false);
      });
  }, [open, locale, filtersKey, filters, pages.length, nextCursor]);

  // Reset the paged accumulator on close (#3000). Without this, closing
  // the modal mid-scroll leaves `pages.length > 0` and `nextCursor > 0`,
  // so the next open's first-page useEffect sees `firstOpen === false`
  // and skips the re-fetch — the user re-opens to whatever tail they
  // had accumulated last time. Bumping `fetchSeqRef` also invalidates
  // any in-flight first-page or loadMore response so it can't land into
  // the freshly-cleared state.
  useEffect(() => {
    if (open) return;
    fetchSeqRef.current += 1;
    setPages([]);
    setMacros([]);
    setNextCursor(0);
    setTotalCountries(0);
    setLoadingMore(false);
  }, [open]);

  // loadMore — fetch the next page and append. Guarded by `loadingMore`
  // so multiple sentinel triggers (fast scroll) collapse to one request.
  const loadMore = useCallback(async () => {
    if (nextCursor == null || loadingMore || loading) return;
    setLoadingMore(true);
    const seq = fetchSeqRef.current;
    try {
      const page = await getGlobalLocationsPage(locale, nextCursor, filters);
      if (fetchSeqRef.current !== seq) return;
      setPages((prev) => [...prev, ...page.countries]);
      setNextCursor(page.nextCursor);
    } finally {
      if (fetchSeqRef.current === seq) setLoadingMore(false);
    }
  }, [locale, filters, nextCursor, loadingMore, loading]);

  // IntersectionObserver — fires loadMore when the bottom sentinel
  // enters the scroll viewport. `rootMargin: 240px` pre-loads one screen
  // before the user actually hits the bottom so the next page is ready
  // by the time it's needed.
  //
  // Track the sentinel as state (via a ref callback that calls
  // `setSentinelEl`) instead of a plain `useRef`, so that its attachment
  // triggers a re-render — and therefore re-runs the observer-setup
  // effect (#3328). The previous implementation keyed the setup effect
  // off `pages.length`, but in the sync-prefetch path the first-page
  // state seeding races Radix Dialog.Portal's deferred content mount: the
  // effect runs with the ref still null, returns early, and never re-runs
  // because no further deps change — leaving the modal's infinite scroll
  // permanently stuck at the first page. Promoting the sentinel to state
  // guarantees we wire up the observer once the node actually attaches.
  const [sentinelEl, setSentinelEl] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open || !sentinelEl) return;
    const root = scrollRef.current;
    if (!root) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) loadMore();
        }
      },
      { root, rootMargin: "240px 0px 240px 0px" },
    );
    observer.observe(sentinelEl);
    return () => observer.disconnect();
  }, [open, sentinelEl, loadMore]);

  useEffect(() => {
    if (!open) {
      setSearch("");
      setSearchHits([]);
    }
  }, [open]);

  // Server-side search — debounced. While the user is typing, we kick a
  // Typesense query against the `location` collection (separate from the
  // country-tier facet) to surface long-tail cities that don't appear in
  // the top-N facet truncation.
  useEffect(() => {
    if (!open) return;
    const q = search.trim();
    if (q.length < 1) {
      setSearchHits([]);
      setSearchLoading(false);
      return;
    }
    setSearchLoading(true);
    const handle = setTimeout(() => {
      const seq = fetchSeqRef.current;
      searchGlobalLocations(q, locale)
        .then((hits) => {
          if (fetchSeqRef.current !== seq) return;
          setSearchHits(hits);
        })
        .finally(() => {
          if (fetchSeqRef.current === seq) setSearchLoading(false);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [open, search, locale]);

  // Filter macros: keep when name OR abbreviation matches the local search
  // text. Once #2939's `aliases[]` field arrives on the location collection,
  // this can grow into substring-against-aliases matching too.
  const filteredMacros = useMemo(() => {
    if (!search.trim()) return macros;
    const q = search.trim().toLowerCase();
    return macros.filter((m) =>
      m.name.toLowerCase().includes(q)
      || m.abbreviation.toLowerCase().includes(q)
      || m.memberCountryNames.some((c) => c.toLowerCase().includes(q)),
    );
  }, [macros, search]);

  const filtered = useMemo(() => {
    const groups = pages;
    if (!search.trim()) return groups;
    const q = search.trim().toLowerCase();

    return groups
      .map((country) => {
        if (country.countryName.toLowerCase().includes(q)) return country;

        const filteredRegions = country.regions
          .map((region) => {
            if (region.regionName?.toLowerCase().includes(q)) return region;
            const locs = region.locations.filter((l) => l.name.toLowerCase().includes(q));
            if (locs.length === 0) return null;
            return { ...region, locations: locs };
          })
          .filter((r): r is NonNullable<typeof r> => r !== null);

        if (filteredRegions.length === 0) return null;
        return { ...country, regions: filteredRegions };
      })
      .filter((g): g is GlobalLocationGroup => g !== null);
  }, [pages, search]);

  // Server-side search hits, deduplicated against the in-memory matches
  // already covered by `filtered` (we don't want to render Berlin twice
  // when it's both in the first page AND a search hit).
  const filteredSearchHits = useMemo(() => {
    if (!search.trim()) return [];
    const inMemoryIds = new Set<number>();
    for (const country of filtered) {
      inMemoryIds.add(country.countryId);
      for (const region of country.regions) {
        if (region.regionId > 0) inMemoryIds.add(region.regionId);
        for (const loc of region.locations) inMemoryIds.add(loc.id);
      }
    }
    return searchHits.filter((h) => !inMemoryIds.has(h.id));
  }, [searchHits, filtered, search]);

  function countCities(country: GlobalLocationGroup) {
    return country.regions.reduce((sum, r) => sum + r.locations.length, 0);
  }

  const showWarning = useCallback((msg: string) => {
    clearTimeout(warningTimer.current);
    setWarning(msg);
    warningTimer.current = setTimeout(() => setWarning(""), 3000);
  }, []);

  const handleSearchKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key !== "Enter") return;
      // Combine macro candidates (matched by canonical name OR abbreviation)
      // with city candidates so Enter on "EU" or "European Union" picks the
      // macro chip directly. Macros are mapped into the same shape as leaf
      // items via `name = canonical`, with the abbreviation surfaced as a
      // second candidate row so abbreviation-typed queries still match.
      const macroCandidates = filteredMacros.flatMap((m) => {
        const items: SelectedLocation[] = [
          { id: m.id, slug: m.slug, name: m.name, type: "macro", parentName: null },
        ];
        if (m.abbreviation && m.abbreviation.toLowerCase() !== m.name.toLowerCase()) {
          items.push({ id: m.id, slug: m.slug, name: m.abbreviation, type: "macro", parentName: null });
        }
        return items;
      });
      const leafItems: SelectedLocation[] = filtered.flatMap((c) =>
        c.regions.flatMap((r) =>
          r.locations.map((l) => ({
            id: l.id,
            slug: l.slug,
            name: l.name,
            type: l.type,
            parentName: r.regionName || c.countryName,
          })),
        ),
      );
      const searchHitItems: SelectedLocation[] = filteredSearchHits.map((h) => ({
        id: h.id,
        slug: h.slug,
        name: h.name,
        type: h.type,
        parentName: h.parentName,
      }));
      const result = findBestGuess(search, [...macroCandidates, ...leafItems, ...searchHitItems]);
      if (!result) return;
      if ("match" in result) {
        // For a macro candidate matched via abbreviation, prefer the
        // canonical display name on the resulting chip — find the macro
        // entry by id and use its `.name`.
        const macro = filteredMacros.find((m) => m.id === result.match.id);
        const final: SelectedLocation = macro
          ? { id: macro.id, slug: macro.slug, name: macro.name, type: "macro", parentName: null }
          : result.match;
        onToggle(final);
        setSearch("");
        setWarning("");
      } else {
        showWarning(t({
          id: "search.bestGuess.ambiguous",
          comment: "Warning when Enter is pressed but multiple items match",
          message: "Multiple matches — select one below",
        }));
      }
    },
    [filtered, filteredMacros, filteredSearchHits, search, onToggle, showWarning, t],
  );

  const hasSearch = search.trim().length > 0;
  // We've completed at least one page fetch when we either have pages OR
  // the server explicitly told us the list is empty (nextCursor === null
  // after fetch). Before that we are in the pre-fetch initial state and
  // must NOT render the "no results" empty state, even though
  // `pages.length === 0` matches — that would flash an empty-state
  // message for a frame between modal mount and the first useEffect's
  // setLoading(true). See #3031.
  const hasFetchedAtLeastOnce = pages.length > 0 || nextCursor === null;
  const isEmpty =
    hasFetchedAtLeastOnce
    && !loading
    && filtered.length === 0
    && filteredMacros.length === 0
    && filteredSearchHits.length === 0
    && !searchLoading;
  // Show the spinner whenever we're explicitly loading OR we're in the
  // pre-fetch initial state (about to fetch but the useEffect hasn't run
  // yet to flip loading). Prevents the empty-state flash described above.
  const showSpinner = loading || (!hasFetchedAtLeastOnce && open);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border-soft bg-surface shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
          aria-describedby={undefined}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-divider px-5 py-4">
            <Dialog.Title className="text-base font-semibold">
              <Trans id="search.locationModal.title" comment="Title for the global location selection modal">
                Select location
              </Trans>
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                className="rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground cursor-pointer"
                aria-label={t({ id: "search.locationModal.close", comment: "Aria label for the location modal close button", message: "Close" })}
              >
                <X size={16} aria-hidden="true" />
              </button>
            </Dialog.Close>
          </div>

          {/* Search */}
          <div className="border-b border-divider px-5 py-3">
            <div className="flex items-center gap-2 rounded-md border border-border-soft px-3 py-2">
              <Search size={14} className="shrink-0 text-muted" />
              <input
                type="text"
                value={search}
                onChange={(e) => { setSearch(e.target.value); setWarning(""); }}
                onKeyDown={handleSearchKeyDown}
                placeholder={t({
                  id: "search.locationModal.searchPlaceholder",
                  comment: "Placeholder for search input in global location modal",
                  message: "Search locations...",
                })}
                className="w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted"
              />
            </div>
            {warning && (
              <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">{warning}</p>
            )}
          </div>

          {/* Body */}
          <ScrollFade wrapperClassName="flex-1 min-h-0" scrollRef={scrollRef} className="px-5 py-4">
            {showSpinner ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={20} className="animate-spin text-muted" />
              </div>
            ) : isEmpty ? (
              <p className="py-8 text-center text-sm text-muted">
                <Trans id="search.locationModal.noResults" comment="No locations match search in location modal">
                  No locations match your search.
                </Trans>
              </p>
            ) : (
              <div className="space-y-5">
                {filteredMacros.length > 0 && (
                  <div>
                    <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
                      <Globe size={14} className="shrink-0" />
                      <Trans id="search.locationModal.regionsHeader" comment="Header for the macro-region cluster (EU, EMEA, DACH) at the top of the location modal">
                        Regions
                      </Trans>
                    </h3>
                    <div className="flex flex-wrap gap-2">
                      {filteredMacros.map((macro) => {
                        const active = selectedIds.has(macro.id);
                        // Macros never have ancestors in our model — only members.
                        // So `isDisabled(macro.id)` is always false today; left in
                        // for future-proofing if macro -> super-macro relationships
                        // are added.
                        if (isDisabled(macro.id) && !active) {
                          return (
                            <DisabledFilterPill
                              key={macro.id}
                              name={macro.name}
                              count={macro.count}
                              ancestorName={ancestorNameOf(macro.id)}
                            />
                          );
                        }
                        const tooltip = macro.memberCountryNames.length > 0
                          ? macro.memberCountryNames.join(", ")
                          : undefined;
                        return (
                          <button
                            key={macro.id}
                            onClick={() => handleToggle({
                              id: macro.id,
                              slug: macro.slug,
                              name: macro.name,
                              type: "macro",
                              parentName: null,
                            })}
                            title={tooltip}
                            className={`inline-flex cursor-pointer items-center gap-1 rounded-full px-3 py-1 text-sm transition-colors ${
                              active
                                ? "bg-primary/10 text-primary"
                                : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                            }`}
                          >
                            {macro.name}
                            <span className={`text-xs ${active ? "text-primary/70" : "text-muted"}`}>
                              ({macro.count})
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Server-side search hits cluster — only when the user is
                    actively searching. Surfaces long-tail cities (e.g.
                    Salzburg) that aren't in the loaded country pages. */}
                {hasSearch && filteredSearchHits.length > 0 && (
                  <div>
                    <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
                      <Search size={14} className="shrink-0" />
                      <Trans id="search.locationModal.searchHitsHeader" comment="Header for the server-side search-results cluster shown when the user types in the location modal search box">
                        Matches
                      </Trans>
                    </h3>
                    <div className="flex flex-wrap gap-2">
                      {filteredSearchHits.map((hit) => {
                        const active = selectedIds.has(hit.id);
                        if (!active && isDisabled(hit.id)) {
                          return (
                            <DisabledFilterPill
                              key={hit.id}
                              name={hit.name}
                              count={hit.count}
                              ancestorName={ancestorNameOf(hit.id)}
                            />
                          );
                        }
                        return (
                          <button
                            key={hit.id}
                            onClick={() => handleToggle({
                              id: hit.id,
                              slug: hit.slug,
                              name: hit.name,
                              type: hit.type,
                              parentName: hit.parentName,
                            })}
                            title={hit.parentName ?? undefined}
                            className={`inline-flex cursor-pointer items-center gap-1 rounded-full px-3 py-1 text-sm transition-colors ${
                              active
                                ? "bg-primary/10 text-primary"
                                : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                            }`}
                          >
                            {hit.name}
                            <span className={`text-xs ${active ? "text-primary/70" : "text-muted"}`}>
                              ({hit.count})
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {filtered.map((country) => {
                  const countryActive = selectedIds.has(country.countryId);
                  const countryDisabled = !countryActive && isDisabled(country.countryId);
                  const showRegions = countCities(country) > REGION_THRESHOLD;
                  // Localized fallback for orphaned cities (no parent
                  // country in hierarchy). The server returns countryName=""
                  // for that case so we don't bake an English "Other" into
                  // the action response.
                  const countryDisplayName = country.countryName || t({
                    id: "search.locationModal.otherCountry",
                    comment: "Fallback label for a country header in the location modal when the city has no parent country in the hierarchy. Rare path.",
                    message: "Other",
                  });

                  return (
                    <div key={country.countryId}>
                      {/* Country header */}
                      {countryDisabled ? (
                        <DisabledFilterPill
                          name={countryDisplayName}
                          count={country.countryCount > 0 ? country.countryCount : undefined}
                          ancestorName={ancestorNameOf(country.countryId)}
                          variant="country"
                          leftAdornment={
                            <CountryFlag iso={countryIso(country.countryId)} size={14} className="mr-1 inline-block align-middle" />
                          }
                        />
                      ) : (
                        <button
                          onClick={() =>
                            handleToggle({
                              id: country.countryId,
                              slug: country.countrySlug,
                              name: countryDisplayName,
                              type: "country",
                              parentName: null,
                            })
                          }
                          className={`mb-2 cursor-pointer text-xs font-semibold uppercase tracking-wider transition-colors ${
                            countryActive ? "text-primary" : "text-muted hover:text-foreground"
                          }`}
                        >
                          <CountryFlag iso={countryIso(country.countryId)} size={14} className="mr-1 inline-block align-middle" />
                          <span className={countryActive ? "underline" : ""}>{countryDisplayName}</span>
                          {country.countryCount > 0 && (
                            <span className={`ml-1 text-[10px] font-normal normal-case ${countryActive ? "text-primary/70" : "text-muted"}`}>
                              ({country.countryCount})
                            </span>
                          )}
                        </button>
                      )}

                      {showRegions ? (
                        <div className="space-y-3 pl-2">
                          {country.regions.map((region) => {
                            if (region.locations.length === 0) return null;
                            const regionActive = region.regionId > 0 && selectedIds.has(region.regionId);
                            const regionDisabled = region.regionId > 0 && !regionActive && isDisabled(region.regionId);
                            return (
                              <div key={region.regionId}>
                                {region.regionId > 0 ? (
                                  regionDisabled ? (
                                    <DisabledFilterPill
                                      name={region.regionName}
                                      count={region.regionCount > 0 ? region.regionCount : undefined}
                                      ancestorName={ancestorNameOf(region.regionId)}
                                      variant="region"
                                    />
                                  ) : (
                                    <button
                                      onClick={() =>
                                        handleToggle({
                                          id: region.regionId,
                                          slug: region.regionSlug,
                                          name: region.regionName,
                                          type: "region",
                                          parentName: country.countryName,
                                        })
                                      }
                                      className={`mb-1.5 cursor-pointer text-xs font-medium transition-colors ${
                                        regionActive ? "text-primary" : "text-muted hover:text-foreground"
                                      }`}
                                    >
                                      <span className={regionActive ? "underline" : ""}>{region.regionName}</span>
                                      {region.regionCount > 0 && (
                                        <span className={`ml-1 text-[10px] font-normal ${regionActive ? "text-primary/70" : "text-muted"}`}>
                                          ({region.regionCount})
                                        </span>
                                      )}
                                    </button>
                                  )
                                ) : (
                                  <span className="mb-1.5 block text-xs font-medium text-muted">
                                    {region.regionName || t({
                                      id: "search.locationModal.otherRegion",
                                      comment: "Fallback label for a region row in the location modal when the region has no display name (e.g. orphaned cities). Rare path; only shown when a city's parent region in the hierarchy has no localized name.",
                                      message: "Other",
                                    })}
                                  </span>
                                )}
                                <div className="flex flex-wrap gap-2">
                                  {region.locations.map((loc) => {
                                    const active = selectedIds.has(loc.id);
                                    if (!active && isDisabled(loc.id)) {
                                      return (
                                        <DisabledFilterPill
                                          key={loc.id}
                                          name={loc.name}
                                          count={loc.count}
                                          ancestorName={ancestorNameOf(loc.id)}
                                        />
                                      );
                                    }
                                    return (
                                      <button
                                        key={loc.id}
                                        onClick={() => handleToggle({ ...loc, parentName: region.regionName || country.countryName })}
                                        className={`inline-flex cursor-pointer items-center gap-1 rounded-full px-3 py-1 text-sm transition-colors ${
                                          active
                                            ? "bg-primary/10 text-primary"
                                            : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                                        }`}
                                      >
                                        {loc.name}
                                        <span className={`text-xs ${active ? "text-primary/70" : "text-muted"}`}>
                                          ({loc.count})
                                        </span>
                                      </button>
                                    );
                                  })}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        <div className="flex flex-wrap gap-2">
                          {country.regions.flatMap((region) =>
                            region.locations.map((loc) => {
                              const active = selectedIds.has(loc.id);
                              if (!active && isDisabled(loc.id)) {
                                return (
                                  <DisabledFilterPill
                                    key={loc.id}
                                    name={loc.name}
                                    count={loc.count}
                                    ancestorName={ancestorNameOf(loc.id)}
                                  />
                                );
                              }
                              return (
                                <button
                                  key={loc.id}
                                  onClick={() => handleToggle({ ...loc, parentName: region.regionName || country.countryName })}
                                  className={`inline-flex cursor-pointer items-center gap-1 rounded-full px-3 py-1 text-sm transition-colors ${
                                    active
                                      ? "bg-primary/10 text-primary"
                                      : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                                  }`}
                                >
                                  {loc.name}
                                  <span className={`text-xs ${active ? "text-primary/70" : "text-muted"}`}>
                                    ({loc.count})
                                  </span>
                                </button>
                              );
                            }),
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}

                {/* Bottom sentinel — IntersectionObserver triggers loadMore
                    when this enters the viewport (with a 240px rootMargin
                    to pre-fetch). Stays mounted until nextCursor is null. */}
                {nextCursor != null && (
                  <div ref={setSentinelEl} className="flex items-center justify-center py-4">
                    {loadingMore && (
                      <Loader2 size={16} className="animate-spin text-muted" />
                    )}
                  </div>
                )}
                {nextCursor == null && pages.length > 0 && totalCountries > LOCATION_PAGE_SIZE && (
                  <p className="py-2 text-center text-xs text-muted/70">
                    <Trans id="search.locationModal.allLoaded" comment="Footer shown after all paged countries have been loaded into the location modal">
                      All {totalCountries} countries loaded
                    </Trans>
                  </p>
                )}
              </div>
            )}
          </ScrollFade>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
