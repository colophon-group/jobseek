"use client";

import type { ReactNode } from "react";
import { MapPin, Briefcase, BarChart3, Code2, DollarSign, Clock, Home, CalendarDays, X } from "lucide-react";
import { useLingui } from "@lingui/react/macro";
import type { WatchlistFilters } from "@/lib/actions/watchlists";
import type { SelectedLocation } from "@/lib/search/types";
import type { WorkMode } from "@/lib/search/types";

type TaxonomyItem = { id: number; slug: string; name: string };
type TFn = ReturnType<typeof useLingui>["t"];

function workModeLabel(t: TFn, value: WorkMode): string {
  switch (value) {
    case "onsite":
      return t({ id: "search.workMode.onsite", comment: "Work mode: onsite (in-office)", message: "On-site" });
    case "hybrid":
      return t({ id: "search.workMode.hybrid", comment: "Work mode: hybrid (mixed onsite/remote)", message: "Hybrid" });
    case "remote":
      return t({ id: "search.workMode.remote", comment: "Work mode: remote (work-from-home)", message: "Remote" });
  }
}

type FilterPill = {
  key: string;
  icon: ReactNode;
  label: string;
  labelClassName?: string;
  onRemove?: () => void;
  removeLabel?: string;
};

export function FilterPillsReadOnly({
  filters,
  locations,
  occupations,
  seniorities,
  technologies,
  workMode,
  employmentType,
  onRemoveKeyword,
  onRemoveLocation,
  onRemoveLocationSlug,
  onRemoveOccupation,
  onRemoveOccupationSlug,
  onRemoveSeniority,
  onRemoveSenioritySlug,
  onRemoveTechnology,
  onRemoveTechnologySlug,
  onToggleEmploymentType,
  onToggleWorkMode,
  onRemoveSalary,
  onRemoveExperience,
  onClearAll,
}: {
  filters: WatchlistFilters;
  locations?: SelectedLocation[];
  occupations?: TaxonomyItem[];
  seniorities?: TaxonomyItem[];
  technologies?: TaxonomyItem[];
  /**
   * Work-mode pills (issue #2983). The watchlist filter shape persists
   * `workMode` on `WatchlistFilters`; callers may either pass the
   * already-validated array here, or rely on `filters.workMode` if they
   * have re-validated it themselves. We accept both so the explore-page
   * "save snapshot" view (which only has the in-memory array, not a
   * persisted filter row) can render too.
   */
  workMode?: WorkMode[];
  /**
   * Employment-type pills (issue #3037). Same defensive pattern as
   * workMode — callers either pre-validate and pass it, or omit and
   * fall back to `filters.employmentType`.
   */
  employmentType?: string[];
  onRemoveKeyword?: (keyword: string) => void;
  onRemoveLocation?: (location: SelectedLocation) => void;
  onRemoveLocationSlug?: (slug: string) => void;
  onRemoveOccupation?: (occupation: TaxonomyItem) => void;
  onRemoveOccupationSlug?: (slug: string) => void;
  onRemoveSeniority?: (seniority: TaxonomyItem) => void;
  onRemoveSenioritySlug?: (slug: string) => void;
  onRemoveTechnology?: (technology: TaxonomyItem) => void;
  onRemoveTechnologySlug?: (slug: string) => void;
  onToggleEmploymentType?: (employmentType: string) => void;
  onToggleWorkMode?: (workMode: WorkMode) => void;
  onRemoveSalary?: () => void;
  onRemoveExperience?: () => void;
  onClearAll?: () => void;
}) {
  const { t } = useLingui();
  const pills: FilterPill[] = [];

  if (filters.keywords?.length) {
    for (const kw of filters.keywords) {
      pills.push({
        key: `kw-${kw}`,
        icon: null,
        label: kw,
        onRemove: onRemoveKeyword ? () => onRemoveKeyword(kw) : undefined,
        removeLabel: onRemoveKeyword
          ? t({ id: "search.filters.removeKeyword", comment: "Aria label for remove-keyword X button; {name} is the keyword", message: `Remove keyword ${kw}` })
          : undefined,
      });
    }
  }
  if (locations && locations.length > 0) {
    for (const loc of locations) {
      const label = loc.parentName && loc.type !== "country" && loc.type !== "macro"
        ? `${loc.name}, ${loc.parentName}`
        : loc.name;
      pills.push({
        key: `loc-${loc.id}`,
        icon: <MapPin size={12} />,
        label,
        onRemove: onRemoveLocation ? () => onRemoveLocation(loc) : undefined,
        removeLabel: onRemoveLocation
          ? t({ id: "search.filters.removeLocation", comment: "Aria label for remove-location X button; {name} is the location label", message: `Remove location ${label}` })
          : undefined,
      });
    }
  } else if (filters.locationSlugs?.length) {
    for (const slug of filters.locationSlugs) {
      pills.push({
        key: `loc-${slug}`,
        icon: <MapPin size={12} />,
        label: slug,
        onRemove: onRemoveLocationSlug ? () => onRemoveLocationSlug(slug) : undefined,
        removeLabel: onRemoveLocationSlug
          ? t({ id: "search.filters.removeLocation", comment: "Aria label for remove-location X button; {name} is the location label", message: `Remove location ${slug}` })
          : undefined,
      });
    }
  }
  if (occupations && occupations.length > 0) {
    for (const occ of occupations) {
      pills.push({
        key: `occ-${occ.id}`,
        icon: <Briefcase size={12} />,
        label: occ.name,
        onRemove: onRemoveOccupation ? () => onRemoveOccupation(occ) : undefined,
        removeLabel: onRemoveOccupation
          ? t({ id: "search.filters.removeFilter", comment: "Aria label for remove-filter X button on a filter pill; {name} is the filter value", message: `Remove ${occ.name} filter` })
          : undefined,
      });
    }
  } else if (filters.occupationSlugs?.length) {
    for (const slug of filters.occupationSlugs) {
      pills.push({
        key: `occ-${slug}`,
        icon: <Briefcase size={12} />,
        label: slug,
        onRemove: onRemoveOccupationSlug ? () => onRemoveOccupationSlug(slug) : undefined,
        removeLabel: onRemoveOccupationSlug
          ? t({ id: "search.filters.removeFilter", comment: "Aria label for remove-filter X button on a filter pill; {name} is the filter value", message: `Remove ${slug} filter` })
          : undefined,
      });
    }
  }
  if (seniorities && seniorities.length > 0) {
    for (const sen of seniorities) {
      pills.push({
        key: `sen-${sen.id}`,
        icon: <BarChart3 size={12} />,
        label: sen.name,
        onRemove: onRemoveSeniority ? () => onRemoveSeniority(sen) : undefined,
        removeLabel: onRemoveSeniority
          ? t({ id: "search.filters.removeFilter", comment: "Aria label for remove-filter X button on a filter pill; {name} is the filter value", message: `Remove ${sen.name} filter` })
          : undefined,
      });
    }
  } else if (filters.senioritySlugs?.length) {
    for (const slug of filters.senioritySlugs) {
      pills.push({
        key: `sen-${slug}`,
        icon: <BarChart3 size={12} />,
        label: slug,
        onRemove: onRemoveSenioritySlug ? () => onRemoveSenioritySlug(slug) : undefined,
        removeLabel: onRemoveSenioritySlug
          ? t({ id: "search.filters.removeFilter", comment: "Aria label for remove-filter X button on a filter pill; {name} is the filter value", message: `Remove ${slug} filter` })
          : undefined,
      });
    }
  }
  if (technologies && technologies.length > 0) {
    for (const tech of technologies) {
      pills.push({
        key: `tech-${tech.id}`,
        icon: <Code2 size={12} />,
        label: tech.name,
        onRemove: onRemoveTechnology ? () => onRemoveTechnology(tech) : undefined,
        removeLabel: onRemoveTechnology
          ? t({ id: "search.filters.removeFilter", comment: "Aria label for remove-filter X button on a filter pill; {name} is the filter value", message: `Remove ${tech.name} filter` })
          : undefined,
      });
    }
  } else if (filters.technologySlugs?.length) {
    for (const slug of filters.technologySlugs) {
      pills.push({
        key: `tech-${slug}`,
        icon: <Code2 size={12} />,
        label: slug,
        onRemove: onRemoveTechnologySlug ? () => onRemoveTechnologySlug(slug) : undefined,
        removeLabel: onRemoveTechnologySlug
          ? t({ id: "search.filters.removeFilter", comment: "Aria label for remove-filter X button on a filter pill; {name} is the filter value", message: `Remove ${slug} filter` })
          : undefined,
      });
    }
  }
  const effectiveEmploymentType = employmentType ?? filters.employmentType;
  if (effectiveEmploymentType && effectiveEmploymentType.length > 0) {
    for (const et of effectiveEmploymentType) {
      pills.push({
        key: `et-${et}`,
        icon: <CalendarDays size={12} />,
        // Same display rule as search-toolbar pills: replace underscores
        // and rely on Tailwind `capitalize` if the consumer wants — we
        // keep it lowercase here to match the unfiltered text rendering
        // and avoid mismatches with the localised labels in the modal.
        label: et.replace(/_/g, " "),
        labelClassName: "capitalize",
        onRemove: onToggleEmploymentType ? () => onToggleEmploymentType(et) : undefined,
        removeLabel: onToggleEmploymentType
          ? t({ id: "search.filters.removeFilter", comment: "Aria label for remove-filter X button on a filter pill; {name} is the filter value", message: `Remove ${et.replace(/_/g, " ")} filter` })
          : undefined,
      });
    }
  }
  if (workMode && workMode.length > 0) {
    for (const mode of workMode) {
      const label = workModeLabel(t, mode);
      pills.push({
        key: `wm-${mode}`,
        icon: <Home size={12} />,
        label,
        onRemove: onToggleWorkMode ? () => onToggleWorkMode(mode) : undefined,
        removeLabel: onToggleWorkMode
          ? t({ id: "search.filters.removeFilter", comment: "Aria label for remove-filter X button on a filter pill; {name} is the filter value", message: `Remove ${label} filter` })
          : undefined,
      });
    }
  }
  if (filters.salaryMin != null || filters.salaryMax != null) {
    const cur = filters.salaryCurrency ?? "EUR";
    let label: string;
    if (filters.salaryMin != null && filters.salaryMax != null) {
      label = `${cur} ${Math.round(filters.salaryMin / 1000)}K – ${Math.round(filters.salaryMax / 1000)}K`;
    } else if (filters.salaryMin != null) {
      label = `${cur} ${Math.round(filters.salaryMin / 1000)}K+`;
    } else {
      label = `${cur} ≤${Math.round(filters.salaryMax! / 1000)}K`;
    }
    pills.push({
      key: "salary",
      icon: <DollarSign size={12} />,
      label,
      onRemove: onRemoveSalary,
      removeLabel: onRemoveSalary
        ? t({ id: "search.filters.removeSalary", comment: "Aria label for the X button that clears the salary-range filter", message: "Remove salary filter" })
        : undefined,
    });
  }
  if (filters.experienceMin != null || filters.experienceMax != null) {
    let label: string;
    if (filters.experienceMin != null && filters.experienceMax != null) {
      label = `${filters.experienceMin}–${filters.experienceMax}y`;
    } else if (filters.experienceMin != null) {
      label = `${filters.experienceMin}y+`;
    } else {
      label = `≤${filters.experienceMax}y`;
    }
    pills.push({
      key: "exp",
      icon: <Clock size={12} />,
      label,
      onRemove: onRemoveExperience,
      removeLabel: onRemoveExperience
        ? t({ id: "search.filters.removeExperience", comment: "Aria label for the X button that clears the experience-range filter", message: "Remove experience filter" })
        : undefined,
    });
  }

  if (pills.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {pills.map((pill) => (
        <span
          key={pill.key}
          className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
        >
          {pill.icon && <span className="shrink-0">{pill.icon}</span>}
          <span className={pill.labelClassName}>{pill.label}</span>
          {pill.onRemove && pill.removeLabel && (
            <button
              type="button"
              onClick={pill.onRemove}
              className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
              aria-label={pill.removeLabel}
            >
              <X size={12} aria-hidden="true" />
            </button>
          )}
        </span>
      ))}
      {onClearAll && (
        <button
          type="button"
          onClick={onClearAll}
          className="cursor-pointer text-xs text-muted transition-colors hover:text-foreground"
        >
          {t({ id: "search.filters.clearAll", comment: "Button to clear all active search filters", message: "Clear all" })}
        </button>
      )}
    </div>
  );
}
