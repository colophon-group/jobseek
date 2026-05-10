"use client";

import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Search, Loader2, Globe } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { getGlobalLocationsGrouped } from "@/lib/actions/locations";
import type { GlobalLocationGroup, GlobalLocationsResponse } from "@/lib/actions/locations";
import { countryIso } from "@/lib/country-flags";
import { CountryFlag } from "@/components/country-flag";
import { findBestGuess } from "./best-guess";
import { ScrollFade } from "@/components/ui/scroll-fade";
import { useDisabledByAncestor, pruneRedundantDescendants } from "./use-disabled-by-ancestor";
import { DisabledFilterPill } from "./disabled-filter-pill";
import { VirtualizedList } from "./virtualized-list";

/** Show region sub-headers when a country has more cities than this. */
const REGION_THRESHOLD = 8;

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
  const [response, setResponse] = useState<GlobalLocationsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [warning, setWarning] = useState("");
  const warningTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Shared with VirtualizedList: ScrollFade owns the overflow:auto element,
  // tanstack-virtual reads scroll offsets from the same node (#2982).
  const scrollRef = useRef<HTMLDivElement>(null);

  const selectedIds = useMemo(() => new Set(selected.map((s) => s.id)), [selected]);

  // Build parent map: city.parent = region (or country if no region),
  // region.parent = country, country.parent = null. Macros are NOT in
  // the parent chain — they're consulted via the macroMembers side-channel.
  const parentMap = useMemo(() => {
    const map = new Map<number, number | null>();
    if (!response) return map;
    for (const country of response.countries) {
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
  }, [response]);

  const macroMembersMap = useMemo(() => {
    const map = new Map<number, number[]>();
    if (!response) return map;
    for (const macro of response.macros) {
      map.set(macro.id, macro.memberCountryIds ?? []);
    }
    return map;
  }, [response]);

  const { isDisabled, disabledByAncestor } = useDisabledByAncestor({
    selectedIds,
    parents: parentMap,
    macroMembers: macroMembersMap,
  });

  // Lookup table id -> localized name, used to render the
  // "Included in <ancestor>" tooltip without re-walking the response.
  const nameById = useMemo(() => {
    const map = new Map<number, string>();
    if (!response) return map;
    for (const macro of response.macros) {
      map.set(macro.id, macro.name);
    }
    for (const country of response.countries) {
      map.set(country.countryId, country.countryName);
      for (const region of country.regions) {
        if (region.regionId > 0) map.set(region.regionId, region.regionName);
        for (const loc of region.locations) map.set(loc.id, loc.name);
      }
    }
    return map;
  }, [response]);

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

  useEffect(() => {
    if (open && (!response || filtersKey !== prevFiltersKeyRef.current)) {
      prevFiltersKeyRef.current = filtersKey;
      setLoading(true);
      getGlobalLocationsGrouped(locale, filters)
        .then(setResponse)
        .finally(() => setLoading(false));
    }
  }, [open, response, locale, filtersKey]);

  useEffect(() => {
    if (!open) setSearch("");
  }, [open]);

  // Filter macros: keep when name OR abbreviation matches the local search
  // text. Once #2939's `aliases[]` field arrives on the location collection,
  // this can grow into substring-against-aliases matching too.
  const filteredMacros = useMemo(() => {
    if (!response) return [];
    if (!search.trim()) return response.macros;
    const q = search.trim().toLowerCase();
    return response.macros.filter((m) =>
      m.name.toLowerCase().includes(q)
      || m.abbreviation.toLowerCase().includes(q)
      || m.memberCountryNames.some((c) => c.toLowerCase().includes(q)),
    );
  }, [response, search]);

  const filtered = useMemo(() => {
    if (!response) return [];
    const groups = response.countries;
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
  }, [response, search]);

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
      const result = findBestGuess(search, [...macroCandidates, ...leafItems]);
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
    [filtered, filteredMacros, search, onToggle, showWarning, t],
  );

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
              <button className="rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground cursor-pointer">
                <X size={16} />
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
          <ScrollFade wrapperClassName="flex-1 min-h-0" className="px-5 py-4" scrollRef={scrollRef}>
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={20} className="animate-spin text-muted" />
              </div>
            ) : filtered.length === 0 && filteredMacros.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted">
                <Trans id="search.locationModal.noResults" comment="No locations match search in location modal">
                  No locations match your search.
                </Trans>
              </p>
            ) : (
              // Country list is virtualized via tanstack-virtual (#2982).
              // The macros cluster is rendered as a `prelude` so it stays
              // mounted at the top — only ~9 chips, no virtualization
              // benefit, and keeping it outside the virtual stream means
              // its layout doesn't perturb country offsets.
              <VirtualizedList
                items={filtered}
                getKey={(c) => c.countryId}
                estimateSize={120}
                overscan={3}
                scrollRef={scrollRef}
                prelude={filteredMacros.length > 0 ? (
                  <div className="mb-5">
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
                ) : null}
                render={(country) => {
                  const countryActive = selectedIds.has(country.countryId);
                  const countryDisabled = !countryActive && isDisabled(country.countryId);
                  const showRegions = countCities(country) > REGION_THRESHOLD;

                  return (
                    <div className="pb-5">
                      {/* Country header */}
                      {countryDisabled ? (
                        <DisabledFilterPill
                          name={country.countryName}
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
                              name: country.countryName,
                              type: "country",
                              parentName: null,
                            })
                          }
                          className={`mb-2 cursor-pointer text-xs font-semibold uppercase tracking-wider transition-colors ${
                            countryActive ? "text-primary" : "text-muted hover:text-foreground"
                          }`}
                        >
                          <CountryFlag iso={countryIso(country.countryId)} size={14} className="mr-1 inline-block align-middle" />
                          <span className={countryActive ? "underline" : ""}>{country.countryName}</span>
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
                                    {region.regionName || "Other"}
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
                }}
              />
            )}
          </ScrollFade>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
