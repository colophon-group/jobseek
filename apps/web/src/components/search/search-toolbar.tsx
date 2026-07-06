"use client";

import type { ReactNode } from "react";
import { X, MapPin, Briefcase, BarChart3, CalendarDays, DollarSign, Clock, Code2, Home } from "lucide-react";
import { useLingui } from "@lingui/react/macro";
import { SearchBar } from "@/components/search/search-bar";
import { AdvancedSearchPanel } from "@/components/search/advanced-search-panel";
import { LanguageNote } from "@/components/search/language-note";
import { SaveSearchButton } from "@/components/search/save-search-button";
import type { SelectedLocation } from "@/lib/search/types";
import type { HistogramFilters, WorkMode } from "@/lib/search";

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
  employmentTypes?: string[];
  onToggleEmploymentType?: (type: string) => void;
  workMode?: WorkMode[];
  onToggleWorkMode?: (mode: WorkMode) => void;
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
  /**
   * Optional element rendered on the right of the language-note row
   * (next to SaveSearchButton when filters are active). Used by the
   * company page to render active/year posting counts inline.
   */
  statsSlot?: ReactNode;
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
  employmentTypes,
  onToggleEmploymentType,
  workMode,
  onToggleWorkMode,
  onSalaryChange,
  onExperienceChange,
  histogramFilters,
  onClearAll,
  onSubmitSearch,
  searchPlaceholder,
  statsSlot,
}: SearchToolbarProps) {
  const { t } = useLingui();

  const hasFilters =
    keywords.length > 0 ||
    locations.length > 0 ||
    occupations.length > 0 ||
    seniorities.length > 0 ||
    (technologies?.length ?? 0) > 0 ||
    (employmentTypes?.length ?? 0) > 0 ||
    (workMode?.length ?? 0) > 0 ||
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
          languages={histogramFilters?.languages}
          companyId={histogramFilters?.companyId}
          userLat={userLat}
          userLng={userLng}
          placeholder={searchPlaceholder}
        />
      </div>
      <div className="flex items-start justify-between gap-4">
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
          employmentTypes={employmentTypes}
          onToggleEmploymentType={onToggleEmploymentType}
          workMode={workMode}
          onToggleWorkMode={onToggleWorkMode}
          onSalaryChange={onSalaryChange}
          onExperienceChange={onExperienceChange}
          histogramFilters={histogramFilters}
        />
        {hasFilters && (
          <SaveSearchButton
            keywords={keywords}
            locations={locations}
            occupations={occupations}
            seniorities={seniorities}
            technologies={technologies}
            employmentTypes={employmentTypes}
            workMode={workMode}
            salaryMin={salaryMin}
            salaryMax={salaryMax}
            salaryCurrency={salaryCurrency}
            experienceMin={experienceMin}
            experienceMax={experienceMax}
          />
        )}
      </div>
      {hasFilters && (
        <div className="flex flex-wrap items-center gap-2">
          {occupations.map((occ) => {
            const name = occ.name;
            return (
              <span
                key={`occ-${occ.id}`}
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
              >
                <Briefcase size={12} className="shrink-0" />
                {name}
                <button
                  onClick={() => onRemoveOccupation(occ.id)}
                  className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
                  aria-label={t({ id: "search.filters.removeFilter", comment: "Aria label for remove-filter X button on a filter pill; {name} is the filter value", message: `Remove ${name} filter` })}
                >
                  <X size={12} aria-hidden="true" />
                </button>
              </span>
            );
          })}
          {seniorities.map((sen) => {
            const name = sen.name;
            return (
              <span
                key={`sen-${sen.id}`}
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
              >
                <BarChart3 size={12} className="shrink-0" />
                {name}
                <button
                  onClick={() => onRemoveSeniority(sen.id)}
                  className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
                  aria-label={t({ id: "search.filters.removeFilter", comment: "Aria label for remove-filter X button on a filter pill; {name} is the filter value", message: `Remove ${name} filter` })}
                >
                  <X size={12} aria-hidden="true" />
                </button>
              </span>
            );
          })}
          {(technologies ?? []).map((tech) => {
            const name = tech.name;
            return (
              <span
                key={`tech-${tech.id}`}
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
              >
                <Code2 size={12} className="shrink-0" />
                {name}
                {onRemoveTechnology && (
                  <button
                    onClick={() => onRemoveTechnology(tech.id)}
                    className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
                    aria-label={t({ id: "search.filters.removeFilter", comment: "Aria label for remove-filter X button on a filter pill; {name} is the filter value", message: `Remove ${name} filter` })}
                  >
                    <X size={12} aria-hidden="true" />
                  </button>
                )}
              </span>
            );
          })}
          {onToggleEmploymentType && employmentTypes && employmentTypes.map((et) => {
            const name = et.replace(/_/g, " ");
            return (
              <span
                key={et}
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm capitalize text-primary"
              >
                <CalendarDays size={12} className="shrink-0" />
                {name}
                <button
                  onClick={() => onToggleEmploymentType(et)}
                  className="ml-0.5 cursor-pointer rounded-full p-0.5 transition-colors hover:bg-primary/20"
                  aria-label={t({ id: "search.filters.removeFilter", comment: "Aria label for remove-filter X button on a filter pill; {name} is the filter value", message: `Remove ${name} filter` })}
                >
                  <X size={12} aria-hidden="true" />
                </button>
              </span>
            );
          })}
          {onToggleWorkMode && workMode && workMode.map((wm) => {
            const name = wm === "onsite"
              ? t({ id: "search.workMode.onsite", comment: "Work mode: onsite (in-office)", message: "On-site" })
              : wm === "hybrid"
                ? t({ id: "search.workMode.hybrid", comment: "Work mode: hybrid (mixed onsite/remote)", message: "Hybrid" })
                : t({ id: "search.workMode.remote", comment: "Work mode: remote (work-from-home)", message: "Remote" });
            return (
              <span
                key={`wm-${wm}`}
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
              >
                <Home size={12} className="shrink-0" />
                {name}
                <button
                  onClick={() => onToggleWorkMode(wm)}
                  className="ml-0.5 cursor-pointer rounded-full p-0.5 transition-colors hover:bg-primary/20"
                  aria-label={t({ id: "search.filters.removeFilter", comment: "Aria label for remove-filter X button on a filter pill; {name} is the filter value", message: `Remove ${name} filter` })}
                >
                  <X size={12} aria-hidden="true" />
                </button>
              </span>
            );
          })}
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
                aria-label={t({ id: "search.filters.removeSalary", comment: "Aria label for the X button that clears the salary-range filter", message: "Remove salary filter" })}
              >
                <X size={12} aria-hidden="true" />
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
                aria-label={t({ id: "search.filters.removeExperience", comment: "Aria label for the X button that clears the experience-range filter", message: "Remove experience filter" })}
              >
                <X size={12} aria-hidden="true" />
              </button>
            </span>
          )}
          {keywords.map((kw) => {
            const name = kw;
            return (
              <span
                key={kw}
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
              >
                {name}
                <button
                  onClick={() => onRemoveKeyword(kw)}
                  className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
                  aria-label={t({ id: "search.filters.removeKeyword", comment: "Aria label for remove-keyword X button; {name} is the keyword", message: `Remove keyword ${name}` })}
                >
                  <X size={12} aria-hidden="true" />
                </button>
              </span>
            );
          })}
          {locations.map((loc) => {
            const name = loc.parentName && loc.type !== "country" && loc.type !== "macro"
              ? `${loc.name}, ${loc.parentName}`
              : loc.name;
            return (
              <span
                key={loc.id}
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
              >
                <MapPin size={12} className="shrink-0" />
                {name}
                <button
                  onClick={() => onRemoveLocation(loc.id)}
                  className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
                  aria-label={t({ id: "search.filters.removeLocation", comment: "Aria label for remove-location X button; {name} is the location label", message: `Remove location ${name}` })}
                >
                  <X size={12} aria-hidden="true" />
                </button>
              </span>
            );
          })}
          <button
            onClick={onClearAll}
            className="cursor-pointer text-xs text-muted transition-colors hover:text-foreground"
          >
            {t({ id: "search.filters.clearAll", comment: "Button to clear all active search filters", message: "Clear all" })}
          </button>
        </div>
      )}
      <div className="flex items-center justify-between gap-4">
        <LanguageNote jobLanguages={jobLanguages} locale={locale} />
        {statsSlot}
      </div>
    </div>
  );
}
