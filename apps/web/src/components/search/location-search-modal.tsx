"use client";

import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Search, Loader2 } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { getGlobalLocationsGrouped } from "@/lib/actions/locations";
import type { GlobalLocationGroup } from "@/lib/actions/locations";
import { countryIso } from "@/lib/country-flags";
import { CountryFlag } from "@/components/country-flag";
import { findBestGuess } from "./best-guess";
import { ScrollFade } from "@/components/ui/scroll-fade";

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
  const [groups, setGroups] = useState<GlobalLocationGroup[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [warning, setWarning] = useState("");
  const warningTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const selectedIds = useMemo(() => new Set(selected.map((s) => s.id)), [selected]);

  const filtersKey = filters ? JSON.stringify(filters) : "";
  const prevFiltersKeyRef = useRef(filtersKey);

  useEffect(() => {
    if (open && (!groups || filtersKey !== prevFiltersKeyRef.current)) {
      prevFiltersKeyRef.current = filtersKey;
      setLoading(true);
      getGlobalLocationsGrouped(locale, filters)
        .then(setGroups)
        .finally(() => setLoading(false));
    }
  }, [open, groups, locale, filtersKey]);

  useEffect(() => {
    if (!open) setSearch("");
  }, [open]);

  const filtered = useMemo(() => {
    if (!groups) return [];
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
  }, [groups, search]);

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
      const leafItems = filtered.flatMap((c) =>
        c.regions.flatMap((r) => r.locations),
      );
      const result = findBestGuess(search, leafItems);
      if (!result) return;
      if ("match" in result) {
        onToggle(result.match);
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
    [filtered, search, onToggle, showWarning, t],
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
          <ScrollFade wrapperClassName="flex-1 min-h-0" className="px-5 py-4">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={20} className="animate-spin text-muted" />
              </div>
            ) : filtered.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted">
                <Trans id="search.locationModal.noResults" comment="No locations match search in location modal">
                  No locations match your search.
                </Trans>
              </p>
            ) : (
              <div className="space-y-5">
                {filtered.map((country) => {
                  const countryActive = selectedIds.has(country.countryId);
                  const showRegions = countCities(country) > REGION_THRESHOLD;

                  return (
                    <div key={country.countryId}>
                      {/* Country header */}
                      <button
                        onClick={() =>
                          onToggle({
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

                      {showRegions ? (
                        <div className="space-y-3 pl-2">
                          {country.regions.map((region) => {
                            if (region.locations.length === 0) return null;
                            const regionActive = region.regionId > 0 && selectedIds.has(region.regionId);
                            return (
                              <div key={region.regionId}>
                                {region.regionId > 0 ? (
                                  <button
                                    onClick={() =>
                                      onToggle({
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
                                ) : (
                                  <span className="mb-1.5 block text-xs font-medium text-muted">
                                    {region.regionName || "Other"}
                                  </span>
                                )}
                                <div className="flex flex-wrap gap-2">
                                  {region.locations.map((loc) => {
                                    const active = selectedIds.has(loc.id);
                                    return (
                                      <button
                                        key={loc.id}
                                        onClick={() => onToggle({ ...loc, parentName: region.regionName || country.countryName })}
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
                              return (
                                <button
                                  key={loc.id}
                                  onClick={() => onToggle({ ...loc, parentName: region.regionName || country.countryName })}
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
