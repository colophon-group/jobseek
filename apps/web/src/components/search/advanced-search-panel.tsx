"use client";

import { useState, useCallback } from "react";
import { SlidersHorizontal, ChevronDown, ChevronUp, MapPin, Briefcase, BarChart3, DollarSign, Clock, Code2 } from "lucide-react";
import { useLingui } from "@lingui/react/macro";
import type { SelectedLocation } from "@/components/search/location-pills";
import { LocationSearchModal } from "./location-search-modal";
import { OccupationModal } from "./occupation-modal";
import { SeniorityModal } from "./seniority-modal";
import { TechnologyModal } from "./technology-modal";
import { SalaryModal } from "./salary-modal";
import { ExperienceModal } from "./experience-modal";
import type { HistogramFilters } from "@/lib/search";

type TaxonomyItem = { id: number; slug: string; name: string };

interface AdvancedSearchPanelProps {
  locale: string;
  userLat?: number;
  userLng?: number;
  locations: SelectedLocation[];
  occupations: TaxonomyItem[];
  seniorities: TaxonomyItem[];
  technologies?: TaxonomyItem[];
  salaryCurrency: string;
  salaryMin: number | undefined;
  salaryMax: number | undefined;
  experienceMin: number | undefined;
  experienceMax: number | undefined;
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
}

export function AdvancedSearchPanel({
  locale,
  locations,
  occupations,
  seniorities,
  technologies,
  salaryCurrency,
  salaryMin,
  salaryMax,
  experienceMin,
  experienceMax,
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
}: AdvancedSearchPanelProps) {
  const { t } = useLingui();
  const [expanded, setExpanded] = useState(false);
  const [locationModalOpen, setLocationModalOpen] = useState(false);
  const [occupationModalOpen, setOccupationModalOpen] = useState(false);
  const [seniorityModalOpen, setSeniorityModalOpen] = useState(false);
  const [technologyModalOpen, setTechnologyModalOpen] = useState(false);
  const [salaryModalOpen, setSalaryModalOpen] = useState(false);
  const [experienceModalOpen, setExperienceModalOpen] = useState(false);

  const handleToggleLocation = useCallback(
    (loc: { id: number; slug: string; name: string; type: string }) => {
      const exists = locations.some((l) => l.id === loc.id);
      if (exists) {
        onRemoveLocation(loc.id);
      } else {
        onAddLocation({
          id: loc.id,
          slug: loc.slug,
          name: loc.name,
          type: loc.type as SelectedLocation["type"],
          parentName: null,
        });
      }
    },
    [locations, onAddLocation, onRemoveLocation],
  );

  const handleToggleOccupation = useCallback(
    (occ: TaxonomyItem) => {
      const exists = occupations.some((o) => o.id === occ.id);
      if (exists) {
        onRemoveOccupation(occ.id);
      } else {
        onAddOccupation(occ);
      }
    },
    [occupations, onAddOccupation, onRemoveOccupation],
  );

  const handleToggleSeniority = useCallback(
    (sen: TaxonomyItem) => {
      const exists = seniorities.some((s) => s.id === sen.id);
      if (exists) {
        onRemoveSeniority(sen.id);
      } else {
        onAddSeniority(sen);
      }
    },
    [seniorities, onAddSeniority, onRemoveSeniority],
  );

  const handleToggleTechnology = useCallback(
    (tech: TaxonomyItem) => {
      const exists = (technologies ?? []).some((t) => t.id === tech.id);
      if (exists) {
        onRemoveTechnology?.(tech.id);
      } else {
        onAddTechnology?.(tech);
      }
    },
    [technologies, onAddTechnology, onRemoveTechnology],
  );

  const btnClass = "flex cursor-pointer items-center gap-2 rounded-md border border-dashed border-border-soft px-3 py-1.5 text-sm text-muted transition-colors hover:border-primary/30 hover:text-foreground";

  return (
    <div>
      <button
        onClick={() => setExpanded((v) => !v)}
        className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted transition-colors hover:text-foreground"
      >
        <SlidersHorizontal size={13} />
        {t({ id: "search.advanced.toggle", comment: "Toggle button for advanced search filters panel", message: "Filters" })}
        {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
      </button>

      {expanded && (
        <div className="mt-2 flex flex-wrap gap-2">
          <button onClick={() => setLocationModalOpen(true)} className={btnClass}>
            <MapPin size={14} className="shrink-0 text-muted" />
            {t({ id: "search.advanced.location", comment: "Label for location filter in advanced search", message: "Location" })}
          </button>
          <button onClick={() => setOccupationModalOpen(true)} className={btnClass}>
            <Briefcase size={14} className="shrink-0 text-muted" />
            {t({ id: "search.advanced.role", comment: "Label for role/occupation filter in advanced search", message: "Role" })}
          </button>
          <button onClick={() => setSeniorityModalOpen(true)} className={btnClass}>
            <BarChart3 size={14} className="shrink-0 text-muted" />
            {t({ id: "search.advanced.level", comment: "Label for seniority/level filter in advanced search", message: "Level" })}
          </button>
          {onAddTechnology && (
            <button onClick={() => setTechnologyModalOpen(true)} className={btnClass}>
              <Code2 size={14} className="shrink-0 text-muted" />
              {t({ id: "search.advanced.technology", comment: "Label for technology filter in advanced search", message: "Technology" })}
            </button>
          )}
          {onSalaryChange && (
            <button onClick={() => setSalaryModalOpen(true)} className={btnClass}>
              <DollarSign size={14} className="shrink-0 text-muted" />
              {t({ id: "search.advanced.salary", comment: "Label for salary filter in advanced search", message: "Salary" })}
            </button>
          )}
          {onExperienceChange && (
            <button onClick={() => setExperienceModalOpen(true)} className={btnClass}>
              <Clock size={14} className="shrink-0 text-muted" />
              {t({ id: "search.advanced.experience", comment: "Label for experience filter in advanced search", message: "Experience" })}
            </button>
          )}
        </div>
      )}

      <LocationSearchModal
        open={locationModalOpen}
        onOpenChange={setLocationModalOpen}
        locale={locale}
        selected={locations.map((l) => ({ id: l.id, slug: l.slug, name: l.name, type: l.type }))}
        onToggle={handleToggleLocation}
        filters={histogramFilters ? {
          companyId: histogramFilters.companyId,
          keywords: histogramFilters.keywords,
          occupationIds: histogramFilters.occupationIds,
          seniorityIds: histogramFilters.seniorityIds,
          technologyIds: histogramFilters.technologyIds,
          languages: histogramFilters.languages,
        } : undefined}
      />
      <OccupationModal
        open={occupationModalOpen}
        onOpenChange={setOccupationModalOpen}
        locale={locale}
        selected={occupations}
        onToggle={handleToggleOccupation}
        filters={histogramFilters ? {
          companyId: histogramFilters.companyId,
          keywords: histogramFilters.keywords,
          locationIds: histogramFilters.locationIds,
          seniorityIds: histogramFilters.seniorityIds,
          technologyIds: histogramFilters.technologyIds,
          languages: histogramFilters.languages,
        } : undefined}
      />
      <SeniorityModal
        open={seniorityModalOpen}
        onOpenChange={setSeniorityModalOpen}
        locale={locale}
        selected={seniorities}
        onToggle={handleToggleSeniority}
        filters={histogramFilters ? {
          companyId: histogramFilters.companyId,
          keywords: histogramFilters.keywords,
          locationIds: histogramFilters.locationIds,
          occupationIds: histogramFilters.occupationIds,
          technologyIds: histogramFilters.technologyIds,
          languages: histogramFilters.languages,
        } : undefined}
      />
      {onAddTechnology && (
        <TechnologyModal
          open={technologyModalOpen}
          onOpenChange={setTechnologyModalOpen}
          selected={technologies ?? []}
          onToggle={handleToggleTechnology}
          filters={histogramFilters ? {
            companyId: histogramFilters.companyId,
            keywords: histogramFilters.keywords,
            locationIds: histogramFilters.locationIds,
            occupationIds: histogramFilters.occupationIds,
            seniorityIds: histogramFilters.seniorityIds,
            languages: histogramFilters.languages,
          } : undefined}
        />
      )}
      {onSalaryChange && (
        <SalaryModal
          open={salaryModalOpen}
          onOpenChange={setSalaryModalOpen}
          currency={salaryCurrency}
          min={salaryMin}
          max={salaryMax}
          onApply={onSalaryChange}
          histogramFilters={histogramFilters}
        />
      )}
      {onExperienceChange && (
        <ExperienceModal
          open={experienceModalOpen}
          onOpenChange={setExperienceModalOpen}
          min={experienceMin}
          max={experienceMax}
          onApply={onExperienceChange}
          histogramFilters={histogramFilters}
        />
      )}
    </div>
  );
}
