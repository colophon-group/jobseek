"use client";

import { useState, useEffect, useMemo } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Search, Loader2 } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { getCompanyLocationsGrouped } from "@/lib/actions/company";
import type { GroupedCompanyLocations, CompanyRegionGroup } from "@/lib/actions/company";
import type { FilterItem } from "./filter-bar";
import { countryIso } from "@/lib/country-flags";
import { CountryFlag } from "@/components/country-flag";

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
  const [groups, setGroups] = useState<GroupedCompanyLocations[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");

  const activeLocationIds = useMemo(
    () => new Set(filters.filter((f) => f.kind === "location").map((f) => f.id)),
    [filters],
  );

  useEffect(() => {
    if (open && !groups) {
      setLoading(true);
      getCompanyLocationsGrouped(companyId, locale)
        .then(setGroups)
        .finally(() => setLoading(false));
    }
  }, [open, groups, companyId, locale]);

  useEffect(() => {
    if (!open) setSearch("");
  }, [open]);

  const filtered = useMemo(() => {
    if (!groups) return [];
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
  }, [groups, search]);

  function toggleLocation(loc: { id: number; slug: string; name: string; type: string }) {
    if (activeLocationIds.has(loc.id)) {
      onFiltersChange(filters.filter((f) => !(f.kind === "location" && f.id === loc.id)));
    } else {
      onFiltersChange([...filters, { kind: "location", id: loc.id, slug: loc.slug, name: loc.name, type: loc.type }]);
    }
  }

  /** Total city count across all regions in a country. */
  function countCities(country: GroupedCompanyLocations) {
    return country.regions.reduce((sum, r) => sum + r.locations.length, 0);
  }

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
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t({
                  id: "company.locationModal.searchPlaceholder",
                  comment: "Placeholder for search input in all-locations modal",
                  message: "Search locations...",
                })}
                className="w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted"
              />
            </div>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto px-5 py-4">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={20} className="animate-spin text-muted" />
              </div>
            ) : filtered.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted">
                <Trans id="company.locationModal.noResults" comment="No locations match search in all-locations modal">
                  No locations match your search.
                </Trans>
              </p>
            ) : (
              <div className="space-y-5">
                {filtered.map((country) => {
                  const countryActive = country.countryId > 0 && activeLocationIds.has(country.countryId);
                  const showRegions = countCities(country) > REGION_THRESHOLD;

                  return (
                    <div key={country.countryId}>
                      {/* Country header */}
                      {country.countryId > 0 ? (
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
                            return (
                              <div key={region.regionId}>
                                {region.regionId > 0 ? (
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
                                ) : (
                                  <span className="mb-1.5 block text-xs font-medium text-muted">
                                    {region.regionName || "Other"}
                                  </span>
                                )}
                                <div className="flex flex-wrap gap-2">
                                  {region.locations.map((loc) => {
                                    const active = activeLocationIds.has(loc.id);
                                    return (
                                      <button
                                        key={loc.id}
                                        onClick={() => toggleLocation(loc)}
                                        className={`inline-flex cursor-pointer items-center gap-1 rounded-full px-2.5 py-0.5 text-xs transition-colors ${
                                          active
                                            ? "bg-primary/10 text-primary"
                                            : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                                        }`}
                                      >
                                        {loc.name}
                                        <span className={`text-[10px] ${active ? "text-primary/70" : "text-muted"}`}>
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
                              return (
                                <button
                                  key={loc.id}
                                  onClick={() => toggleLocation(loc)}
                                  className={`inline-flex cursor-pointer items-center gap-1 rounded-full px-2.5 py-0.5 text-xs transition-colors ${
                                    active
                                      ? "bg-primary/10 text-primary"
                                      : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                                  }`}
                                >
                                  {loc.name}
                                  <span className={`text-[10px] ${active ? "text-primary/70" : "text-muted"}`}>
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
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
