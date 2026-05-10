"use client";

import { MapPin, Briefcase, BarChart3, Code2, DollarSign, Clock, Home } from "lucide-react";
import type { WatchlistFilters } from "@/lib/actions/watchlists";
import type { SelectedLocation } from "@/components/search/location-pills";
import type { WorkMode } from "@/lib/search/types";

type TaxonomyItem = { id: number; slug: string; name: string };

const WORK_MODE_LABEL: Record<WorkMode, string> = {
  onsite: "On-site",
  hybrid: "Hybrid",
  remote: "Remote",
};

export function FilterPillsReadOnly({
  filters,
  locations,
  occupations,
  seniorities,
  technologies,
  workMode,
}: {
  filters: WatchlistFilters;
  locations?: SelectedLocation[];
  occupations?: TaxonomyItem[];
  seniorities?: TaxonomyItem[];
  technologies?: TaxonomyItem[];
  /**
   * Optional work-mode filter slugs to render as read-only pills
   * (issue #2983). The watchlist filter shape doesn't yet persist
   * work-mode, but the prop is accepted now so the explore-page
   * "save snapshot" view can show it.
   */
  workMode?: WorkMode[];
}) {
  const pills: { key: string; icon: React.ReactNode; label: string }[] = [];

  if (filters.keywords?.length) {
    for (const kw of filters.keywords) {
      pills.push({ key: `kw-${kw}`, icon: null, label: kw });
    }
  }
  if (locations && locations.length > 0) {
    for (const loc of locations) {
      const label = loc.parentName && loc.type !== "country" && loc.type !== "macro"
        ? `${loc.name}, ${loc.parentName}`
        : loc.name;
      pills.push({ key: `loc-${loc.id}`, icon: <MapPin size={12} />, label });
    }
  } else if (filters.locationSlugs?.length) {
    for (const slug of filters.locationSlugs) {
      pills.push({ key: `loc-${slug}`, icon: <MapPin size={12} />, label: slug });
    }
  }
  if (occupations && occupations.length > 0) {
    for (const occ of occupations) {
      pills.push({ key: `occ-${occ.id}`, icon: <Briefcase size={12} />, label: occ.name });
    }
  } else if (filters.occupationSlugs?.length) {
    for (const slug of filters.occupationSlugs) {
      pills.push({ key: `occ-${slug}`, icon: <Briefcase size={12} />, label: slug });
    }
  }
  if (seniorities && seniorities.length > 0) {
    for (const sen of seniorities) {
      pills.push({ key: `sen-${sen.id}`, icon: <BarChart3 size={12} />, label: sen.name });
    }
  } else if (filters.senioritySlugs?.length) {
    for (const slug of filters.senioritySlugs) {
      pills.push({ key: `sen-${slug}`, icon: <BarChart3 size={12} />, label: slug });
    }
  }
  if (technologies && technologies.length > 0) {
    for (const tech of technologies) {
      pills.push({ key: `tech-${tech.id}`, icon: <Code2 size={12} />, label: tech.name });
    }
  } else if (filters.technologySlugs?.length) {
    for (const slug of filters.technologySlugs) {
      pills.push({ key: `tech-${slug}`, icon: <Code2 size={12} />, label: slug });
    }
  }
  if (workMode && workMode.length > 0) {
    for (const mode of workMode) {
      pills.push({ key: `wm-${mode}`, icon: <Home size={12} />, label: WORK_MODE_LABEL[mode] });
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
    pills.push({ key: "salary", icon: <DollarSign size={12} />, label });
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
    pills.push({ key: "exp", icon: <Clock size={12} />, label });
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
          {pill.label}
        </span>
      ))}
    </div>
  );
}
