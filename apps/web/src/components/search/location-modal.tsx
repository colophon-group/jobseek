"use client";

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Search, Loader2, Globe } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { getCompanyLocationsGroupedWithMacros } from "@/lib/actions/company";
import type {
  CompanyLocationsResponse,
  GroupedCompanyLocations,
  CompanyRegionGroup,
} from "@/lib/actions/company";
import type { FilterItem } from "./filter-bar";
import { countryIso } from "@/lib/country-flags";
import { CountryFlag } from "@/components/country-flag";
import { findBestGuess } from "./best-guess";
import { ScrollFade } from "@/components/ui/scroll-fade";
import { useDisabledByAncestor } from "./use-disabled-by-ancestor";
import { DisabledFilterPill } from "./disabled-filter-pill";

/** Threshold: show region sub-headers when a country has more cities than this. */
const REGION_THRESHOLD = 8;

interface LocationModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  companyId: string;
  locale: string;
  filters: FilterItem[];
  onFiltersChange: (filters: FilterItem[]) => void;
}

export function LocationModal({
  open,
  onOpenChange,
  companyId,
  locale,
  filters,
  onFiltersChange,
}: LocationModalProps) {
  const { t } = useLingui();
  const [response, setResponse] = useState<CompanyLocationsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [warning, setWarning] = useState("");
  const warningTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const activeLocationIds = useMemo(
    () => new Set(filters.filter((f) => f.kind === "location").map((f) => f.id)),
    [filters],
  );

  useEffect(() => {
    if (open && !response) {
      setLoading(true);
      getCompanyLocationsGroupedWithMacros(companyId, locale)
        .then(setResponse)
        .finally(() => setLoading(false));
    }
  }, [open, response, companyId, locale]);

  useEffect(() => {
    if (!open) setSearch("");
  }, [open]);

  // Filter macros: keep when canonical name OR abbreviation OR any member
  // country name matches the local search. Mirrors the global modal —
  // depends on #2939's `aliases[]` for richer alias matching once that
  // ships.
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
    const matches = (aliases?: string[]) => aliases?.some((a) => a.includes(q)) ?? false;

    return groups
      .map((country) => {
        const countryMatch =
          country.countryName.toLowerCase().includes(q) || matches(country.countryAliases);
        if (countryMatch) return country;

        // Filter regions/cities
        const filteredRegions = country.regions
          .map((region) => {
            const regionMatch =
              (region.regionName?.toLowerCase().includes(q)) || matches(region.regionAliases);
            if (regionMatch) return region;
            const locs = region.locations.filter(
              (l) => l.name.toLowerCase().includes(q) || matches(l.aliases),
            );
            if (locs.length === 0) return null;
            return { ...region, locations: locs };
          })
          .filter((r): r is CompanyRegionGroup => r !== null);

        if (filteredRegions.length === 0) return null;
        return { ...country, regions: filteredRegions };
      })
      .filter((g): g is GroupedCompanyLocations => g !== null);
  }, [response, search]);

  // Build hierarchy maps from the loaded response. Same shape as the
  // global modal — see use-disabled-by-ancestor.ts for the contract.
  const parentMap = useMemo(() => {
    const map = new Map<number, number | null>();
    if (!response) return map;
    for (const country of response.countries) {
      if (country.countryId > 0) map.set(country.countryId, null);
      for (const region of country.regions) {
        if (region.regionId > 0 && country.countryId > 0) {
          map.set(region.regionId, country.countryId);
        }
        for (const loc of region.locations) {
          map.set(
            loc.id,
            region.regionId > 0 ? region.regionId : country.countryId > 0 ? country.countryId : null,
          );
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
    selectedIds: activeLocationIds,
    parents: parentMap,
    macroMembers: macroMembersMap,
  });

  const nameById = useMemo(() => {
    const map = new Map<number, string>();
    if (!response) return map;
    for (const macro of response.macros) map.set(macro.id, macro.name);
    for (const country of response.countries) {
      if (country.countryId > 0) map.set(country.countryId, country.countryName);
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

  const toggleLocation = useCallback((loc: { id: number; slug: string; name: string; type: string }) => {
    if (activeLocationIds.has(loc.id)) {
      onFiltersChange(filters.filter((f) => !(f.kind === "location" && f.id === loc.id)));
      return;
    }
    // Selecting a parent: remove redundant descendants from the filters
    // so the chip strip doesn't carry stale selections that would render
    // as disabled-but-selected.
    const next: FilterItem[] = [
      ...filters,
      { kind: "location", id: loc.id, slug: loc.slug, name: loc.name, type: loc.type },
    ];
    // Find descendants of `loc.id` in current selection. A descendant is
    // anything whose parent chain reaches `loc.id`, OR (when `loc` is a
    // macro) any country in macroMembers + their descendants.
    const subsumed = new Set<number>();
    const macroChildren = macroMembersMap.get(loc.id);
    if (macroChildren) {
      for (const cid of macroChildren) subsumed.add(cid);
    }
    for (const id of activeLocationIds) {
      let cur = parentMap.get(id);
      const seen = new Set<number>([id]);
      while (cur != null && !seen.has(cur)) {
        seen.add(cur);
        if (cur === loc.id) { subsumed.add(id); break; }
        if (subsumed.has(cur)) { subsumed.add(id); break; }
        cur = parentMap.get(cur);
      }
    }
    const cleaned = next.filter((f) => f.kind !== "location" || !subsumed.has(f.id));
    onFiltersChange(cleaned);
  }, [activeLocationIds, filters, onFiltersChange, parentMap, macroMembersMap]);

  /** Total city count across all regions in a country. */
  function countCities(country: GroupedCompanyLocations) {
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
      // Combine macros (canonical name + abbreviation) with city leaf items
      // so Enter on "EU" or "European Union" picks the macro chip directly.
      const macroCandidates = filteredMacros.flatMap((m) => {
        const items: { id: number; slug: string; name: string; type: string }[] = [
          { id: m.id, slug: m.slug, name: m.name, type: "macro" },
        ];
        if (m.abbreviation && m.abbreviation.toLowerCase() !== m.name.toLowerCase()) {
          items.push({ id: m.id, slug: m.slug, name: m.abbreviation, type: "macro" });
        }
        return items;
      });
      const leafItems = filtered.flatMap((c) =>
        c.regions.flatMap((r) => r.locations),
      );
      const result = findBestGuess(search, [...macroCandidates, ...leafItems]);
      if (!result) return;
      if ("match" in result) {
        // Always use the canonical display name on the resulting chip when
        // the matched item is a macro (so abbreviation queries still yield
        // "European Union" rather than "EU" on the filter pill).
        const macro = filteredMacros.find((m) => m.id === result.match.id);
        const final = macro
          ? { id: macro.id, slug: macro.slug, name: macro.name, type: "macro" }
          : result.match;
        toggleLocation(final);
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
    [filtered, filteredMacros, search, toggleLocation, showWarning, t],
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
              <Trans id="company.locationModal.title" comment="Title for the all-locations modal on company page">
                All locations
              </Trans>
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                className="rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground cursor-pointer"
                aria-label={t({ id: "company.locationModal.close", comment: "Aria label for close button on the company-page all-locations modal", message: "Close" })}
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
                  id: "company.locationModal.searchPlaceholder",
                  comment: "Placeholder for search input in all-locations modal",
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
          <ScrollFade wrapperClassName="flex-1 min-h-0" className="px-5 py-4">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={20} className="animate-spin text-muted" />
              </div>
            ) : filtered.length === 0 && filteredMacros.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted">
                <Trans id="company.locationModal.noResults" comment="No locations match search in all-locations modal">
                  No locations match your search.
                </Trans>
              </p>
            ) : (
              <div className="space-y-5">
                {filteredMacros.length > 0 && (
                  <div>
                    <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
                      <Globe size={14} className="shrink-0" />
                      <Trans id="company.locationModal.regionsHeader" comment="Header for the macro-region cluster (EU, EMEA, DACH) in company-page location modal">
                        Regions
                      </Trans>
                    </h3>
                    <div className="flex flex-wrap gap-2">
                      {filteredMacros.map((macro) => {
                        const active = activeLocationIds.has(macro.id);
                        if (!active && isDisabled(macro.id)) {
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
                            onClick={() => toggleLocation({
                              id: macro.id,
                              slug: macro.slug,
                              name: macro.name,
                              type: "macro",
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
                {filtered.map((country) => {
                  const countryActive = country.countryId > 0 && activeLocationIds.has(country.countryId);
                  const countryDisabled = country.countryId > 0 && !countryActive && isDisabled(country.countryId);
                  const showRegions = countCities(country) > REGION_THRESHOLD;

                  return (
                    <div key={country.countryId}>
                      {/* Country header */}
                      {country.countryId > 0 ? (
                        countryDisabled ? (
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
                            onClick={() => toggleLocation({
                              id: country.countryId,
                              slug: country.countrySlug,
                              name: country.countryName,
                              type: "country",
                            })}
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
                        )
                      ) : (
                        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
                          <CountryFlag iso={countryIso(country.countryId)} size={14} className="mr-1 inline-block align-middle" />
                          {country.countryName}
                        </h3>
                      )}

                      {showRegions ? (
                        /* Region sub-groups */
                        <div className="space-y-3 pl-2">
                          {country.regions.map((region) => {
                            if (region.locations.length === 0) return null;
                            const regionActive = region.regionId > 0 && activeLocationIds.has(region.regionId);
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
                                      onClick={() => toggleLocation({
                                        id: region.regionId,
                                        slug: region.regionSlug,
                                        name: region.regionName,
                                        type: "region",
                                      })}
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
                                    const active = activeLocationIds.has(loc.id);
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
                                        onClick={() => toggleLocation(loc)}
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
                        /* Flat city list (few cities) */
                        <div className="flex flex-wrap gap-2">
                          {country.regions.flatMap((region) =>
                            region.locations.map((loc) => {
                              const active = activeLocationIds.has(loc.id);
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
                                  onClick={() => toggleLocation(loc)}
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
              </div>
            )}
          </ScrollFade>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
