"use client";

import { X, MapPin, Briefcase, BarChart3, DollarSign, Clock, Code2 } from "lucide-react";
import { useLingui } from "@lingui/react/macro";
import { SearchBar } from "@/components/search/search-bar";
import { AdvancedSearchPanel } from "@/components/search/advanced-search-panel";
import { LanguageNote } from "@/components/search/language-note";
import type { SelectedLocation } from "@/components/search/location-pills";
import type { HistogramFilters } from "@/lib/search";

type TaxonomyItem = { id: number; slug: string; name: string };

interface SearchToolbarProps {
  locale: string;
  userLat?: number;
  userLng?: number;
  // Filter state
  keywords: string[];
  locations: SelectedLocation[];
  occupations: TaxonomyItem[];
  seniorities: TaxonomyItem[];
  technologies?: TaxonomyItem[];
  salaryCurrency?: string;
  salaryMin?: number;
  salaryMax?: number;
  experienceMin?: number;
  experienceMax?: number;
  // Language
  jobLanguages: string[];
  // Callbacks
  onRemoveKeyword: (keyword: string) => void;
  onAddLocation: (loc: SelectedLocation) => void;
  onRemoveLocation: (id: number) => void;
  onAddOccupation: (occ: TaxonomyItem) => void;
  onRemoveOccupation: (id: number) => void;
  onAddSeniority: (sen: TaxonomyItem) => void;
  onRemoveSeniority: (id: number) => void;
  onAddTechnology?: (tech: TaxonomyItem) => void;
  onRemoveTechnology?: (id: number) => void;
  onSalaryChange?: (currency: string, min: number | undefined, max: number | undefined) => void;
  onExperienceChange?: (min: number | undefined, max: number | undefined) => void;
  histogramFilters?: HistogramFilters;
  onClearAll: () => void;
  onSubmitSearch: (
    keywords: string[],
    locations: SelectedLocation[],
    occupations?: TaxonomyItem[],
    seniorities?: TaxonomyItem[],
    technologies?: TaxonomyItem[],
  ) => void;
  /** Placeholder for the mobile search bar */
  searchPlaceholder?: string;
}

export function SearchToolbar({
  locale,
  userLat,
  userLng,
  keywords,
  locations,
  occupations,
  seniorities,
  technologies,
  salaryCurrency,
  salaryMin,
  salaryMax,
  experienceMin,
  experienceMax,
  jobLanguages,
  onRemoveKeyword,
  onAddLocation,
  onRemoveLocation,
  onAddOccupation,
  onRemoveOccupation,
  onAddSeniority,
  onRemoveSeniority,
  onAddTechnology,
  onRemoveTechnology,
  onSalaryChange,
  onExperienceChange,
  histogramFilters,
  onClearAll,
  onSubmitSearch,
  searchPlaceholder,
}: SearchToolbarProps) {
  const { t } = useLingui();

  const hasFilters =
    keywords.length > 0 ||
    locations.length > 0 ||
    occupations.length > 0 ||
    seniorities.length > 0 ||
    (technologies?.length ?? 0) > 0 ||
    salaryMin != null ||
    salaryMax != null ||
    experienceMin != null ||
    experienceMax != null;

  return (
    <div className="space-y-3">
      {/* Mobile-only: search bar is in the header on desktop */}
      <div className="md:hidden">
        <SearchBar
          onAddLocation={onAddLocation}
          onAddOccupation={onAddOccupation}
          onAddSeniority={onAddSeniority}
          onAddTechnology={onAddTechnology}
          onSubmitSearch={onSubmitSearch}
          locale={locale}
          keywords={keywords}
          locations={locations}
          occupations={occupations}
          seniorities={seniorities}
          technologies={technologies}
          userLat={userLat}
          userLng={userLng}
          placeholder={searchPlaceholder}
        />
      </div>
      <AdvancedSearchPanel
        locale={locale}
        userLat={userLat}
        userLng={userLng}
        locations={locations}
        occupations={occupations}
        seniorities={seniorities}
        technologies={technologies}
        salaryCurrency={salaryCurrency ?? "EUR"}
        salaryMin={salaryMin}
        salaryMax={salaryMax}
        experienceMin={experienceMin}
        experienceMax={experienceMax}
        onAddLocation={onAddLocation}
        onRemoveLocation={onRemoveLocation}
        onAddOccupation={onAddOccupation}
        onRemoveOccupation={onRemoveOccupation}
        onAddSeniority={onAddSeniority}
        onRemoveSeniority={onRemoveSeniority}
        onAddTechnology={onAddTechnology}
        onRemoveTechnology={onRemoveTechnology}
        onSalaryChange={onSalaryChange}
        onExperienceChange={onExperienceChange}
        histogramFilters={histogramFilters}
      />
      {hasFilters && (
        <div className="flex flex-wrap items-center gap-2">
          {occupations.map((occ) => (
            <span
              key={`occ-${occ.id}`}
              className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
            >
              <Briefcase size={12} className="shrink-0" />
              {occ.name}
              <button
                onClick={() => onRemoveOccupation(occ.id)}
                className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
              >
                <X size={12} />
              </button>
            </span>
          ))}
          {seniorities.map((sen) => (
            <span
              key={`sen-${sen.id}`}
              className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
            >
              <BarChart3 size={12} className="shrink-0" />
              {sen.name}
              <button
                onClick={() => onRemoveSeniority(sen.id)}
                className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
              >
                <X size={12} />
              </button>
            </span>
          ))}
          {(technologies ?? []).map((tech) => (
            <span
              key={`tech-${tech.id}`}
              className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
            >
              <Code2 size={12} className="shrink-0" />
              {tech.name}
              {onRemoveTechnology && (
                <button
                  onClick={() => onRemoveTechnology(tech.id)}
                  className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
                >
                  <X size={12} />
                </button>
              )}
            </span>
          ))}
          {onSalaryChange && (salaryMin != null || salaryMax != null) && (
            <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary">
              <DollarSign size={12} className="shrink-0" />
              {salaryMin != null && salaryMax != null
                ? `${salaryCurrency} ${Math.round(salaryMin / 1000)}K – ${Math.round(salaryMax / 1000)}K`
                : salaryMin != null
                  ? `${salaryCurrency} ${Math.round(salaryMin / 1000)}K+`
                  : `${salaryCurrency} ≤${Math.round(salaryMax! / 1000)}K`}
              <button
                onClick={() => onSalaryChange(salaryCurrency ?? "EUR", undefined, undefined)}
                className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
              >
                <X size={12} />
              </button>
            </span>
          )}
          {onExperienceChange && (experienceMin != null || experienceMax != null) && (
            <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary">
              <Clock size={12} className="shrink-0" />
              {experienceMin != null && experienceMax != null
                ? `${experienceMin}–${experienceMax}y`
                : experienceMin != null
                  ? `${experienceMin}y+`
                  : `≤${experienceMax}y`}
              <button
                onClick={() => onExperienceChange(undefined, undefined)}
                className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
              >
                <X size={12} />
              </button>
            </span>
          )}
          {keywords.map((kw) => (
            <span
              key={kw}
              className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
            >
              {kw}
              <button
                onClick={() => onRemoveKeyword(kw)}
                className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
              >
                <X size={12} />
              </button>
            </span>
          ))}
          {locations.map((loc) => (
            <span
              key={loc.id}
              className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
            >
              <MapPin size={12} className="shrink-0" />
              {loc.parentName && loc.type !== "country" && loc.type !== "macro"
                ? `${loc.name}, ${loc.parentName}`
                : loc.name}
              <button
                onClick={() => onRemoveLocation(loc.id)}
                className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
              >
                <X size={12} />
              </button>
            </span>
          ))}
          <button
            onClick={onClearAll}
            className="cursor-pointer text-xs text-muted transition-colors hover:text-foreground"
          >
            {t({ id: "search.filters.clearAll", comment: "Button to clear all active search filters", message: "Clear all" })}
          </button>
        </div>
      )}
      <LanguageNote jobLanguages={jobLanguages} locale={locale} />
    </div>
  );
}
