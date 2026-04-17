"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { X, Loader2, MapPin, Briefcase, BarChart3, Code2, DollarSign, Clock, Building2, Pencil } from "lucide-react";
import { BackLink } from "@/components/BackLink";
import { useLingui } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";
import { useAuth } from "@/lib/useAuth";
import type {
  WatchlistDetail,
  WatchlistFilters,
  WatchlistPostingEntry,
} from "@/lib/actions/watchlists";
import {
  updateWatchlist,
  addCompanyToWatchlist,
  removeCompanyFromWatchlist,
  clearWatchlistCompanies,
} from "@/lib/actions/watchlists";
import { CompanyPill } from "@/components/watchlist/company-pill";
import { CompanySearchModal } from "@/components/watchlist/company-search-modal";
import { WatchlistActionBar } from "@/components/watchlist/watchlist-action-bar";
import { WatchlistJobList } from "@/components/watchlist/watchlist-job-list";
import { FilterPillsReadOnly } from "@/components/search/filter-pills-readonly";
import { AdvancedSearchPanel } from "@/components/search/advanced-search-panel";
import type { SelectedLocation } from "@/components/search/location-pills";
import type { HistogramFilters } from "@/lib/search";

type Company = { id: string; name: string; slug: string; icon: string | null };
type TaxonomyItem = { id: number; slug: string; name: string };

export function WatchlistViewPage({
  detail,
  isOwner,
  isPaidPlan,
  limitReached,
  initialPostings,
  initialTotal,
  yearTotal,
  locale,
  resolvedLocations,
  resolvedOccupations,
  resolvedSeniorities,
  resolvedTechnologies,
  jobLanguages,
  languages,
}: {
  detail: WatchlistDetail;
  isOwner: boolean;
  isPaidPlan: boolean;
  limitReached: boolean;
  initialPostings: WatchlistPostingEntry[];
  initialTotal: number;
  yearTotal: number;
  locale: string;
  resolvedLocations: SelectedLocation[];
  resolvedOccupations: TaxonomyItem[];
  resolvedSeniorities: TaxonomyItem[];
  resolvedTechnologies: TaxonomyItem[];
  jobLanguages: string[];
  languages: string[];
}) {
  const { t } = useLingui();
  const router = useRouter();
  const lp = useLocalePath();
  const { user } = useAuth();

  // ── Editable title ──
  const [title, setTitle] = useState(detail.title);
  const [editingTitle, setEditingTitle] = useState(false);
  const [savingTitle, setSavingTitle] = useState(false);
  const titleInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingTitle) titleInputRef.current?.focus();
  }, [editingTitle]);

  const saveTitle = useCallback(async () => {
    const trimmed = title.trim();
    if (!trimmed || trimmed === detail.title) {
      setTitle(detail.title);
      setEditingTitle(false);
      return;
    }
    setSavingTitle(true);
    const result = await updateWatchlist({ watchlistId: detail.id, title: trimmed });
    setSavingTitle(false);
    setEditingTitle(false);
    if ("slug" in result && user?.username) {
      router.replace(lp(`/${user.username}/${result.slug}`));
    }
  }, [title, detail.title, detail.id, user?.username, router, lp]);

  // ── Editable description ──
  const [description, setDescription] = useState(detail.description ?? "");
  const [editingDescription, setEditingDescription] = useState(false);
  const [savingDescription, setSavingDescription] = useState(false);
  const descriptionRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (editingDescription) {
      const el = descriptionRef.current;
      if (el) {
        el.focus();
        el.selectionStart = el.value.length;
      }
    }
  }, [editingDescription]);

  const saveDescription = useCallback(async () => {
    const trimmed = description.trim();
    if (trimmed === (detail.description ?? "")) {
      setEditingDescription(false);
      return;
    }
    setSavingDescription(true);
    await updateWatchlist({
      watchlistId: detail.id,
      description: trimmed || null,
    });
    setSavingDescription(false);
    setEditingDescription(false);
  }, [description, detail.description, detail.id]);

  // ── Editable companies ──
  const [companies, setCompanies] = useState<Company[]>(detail.companies);
  const [anyCompany, setAnyCompany] = useState(detail.filters.anyCompany ?? false);
  const [companyModalOpen, setCompanyModalOpen] = useState(false);

  function handleToggleCompany(company: Company) {
    const exists = companies.some((c) => c.id === company.id);
    if (exists) {
      setCompanies((prev) => prev.filter((c) => c.id !== company.id));
      removeCompanyFromWatchlist(detail.id, company.id);
    } else {
      setCompanies((prev) => [...prev, company]);
      addCompanyToWatchlist(detail.id, company.id);
    }
  }

  function handleRemoveCompany(companyId: string) {
    setCompanies((prev) => prev.filter((c) => c.id !== companyId));
    removeCompanyFromWatchlist(detail.id, companyId);
  }

  function handleClearAllCompanies() {
    setCompanies([]);
    clearWatchlistCompanies(detail.id);
  }

  // ── Editable filters (using resolved objects) ──
  const [keywords, setKeywords] = useState<string[]>(detail.filters.keywords ?? []);
  const [locations, setLocations] = useState<SelectedLocation[]>(resolvedLocations);
  const [occupations, setOccupations] = useState<TaxonomyItem[]>(resolvedOccupations);
  const [seniorities, setSeniorities] = useState<TaxonomyItem[]>(resolvedSeniorities);
  const [technologies, setTechnologies] = useState<TaxonomyItem[]>(resolvedTechnologies);
  const [salaryCurrency, setSalaryCurrency] = useState<string>(detail.filters.salaryCurrency ?? "EUR");
  const [salaryMin, setSalaryMin] = useState<number | undefined>(detail.filters.salaryMin);
  const [salaryMax, setSalaryMax] = useState<number | undefined>(detail.filters.salaryMax);
  const [experienceMin, setExperienceMin] = useState<number | undefined>(detail.filters.experienceMin);
  const [experienceMax, setExperienceMax] = useState<number | undefined>(detail.filters.experienceMax);

  const histogramFilters: HistogramFilters = useMemo(() => ({
    locationIds: locations.length > 0 ? locations.map((l) => l.id) : undefined,
    occupationIds: occupations.length > 0 ? occupations.map((o) => o.id) : undefined,
    seniorityIds: seniorities.length > 0 ? seniorities.map((s) => s.id) : undefined,
    technologyIds: technologies.length > 0 ? technologies.map((t) => t.id) : undefined,
    languages: languages.length > 0 ? languages : undefined,
  }), [locations, occupations, seniorities, technologies, languages]);

  // Persist filters to DB (debounced, cleaned up on unmount)
  const saveFiltersTimeout = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => {
    return () => clearTimeout(saveFiltersTimeout.current);
  }, []);
  function persistFilters(updated: WatchlistFilters) {
    clearTimeout(saveFiltersTimeout.current);
    saveFiltersTimeout.current = setTimeout(() => {
      updateWatchlist({ watchlistId: detail.id, filters: updated });
    }, 500);
  }

  function handleToggleAnyCompany() {
    const next = !anyCompany;
    setAnyCompany(next);
    persistFilters(buildFilters({ ac: next }));
  }

  function buildFilters(overrides: Partial<{
    kw: string[]; locs: SelectedLocation[]; occs: TaxonomyItem[];
    sens: TaxonomyItem[]; techs: TaxonomyItem[];
    salCur: string; salMin: number | undefined; salMax: number | undefined;
    expMin: number | undefined; expMax: number | undefined;
    ac: boolean;
  }> = {}): WatchlistFilters {
    const kw = overrides.kw ?? keywords;
    const locs = overrides.locs ?? locations;
    const occs = overrides.occs ?? occupations;
    const sens = overrides.sens ?? seniorities;
    const techs = overrides.techs ?? technologies;
    const ac = overrides.ac ?? anyCompany;
    return {
      keywords: kw.length > 0 ? kw : undefined,
      locationSlugs: locs.length > 0 ? locs.map((l) => l.slug) : undefined,
      occupationSlugs: occs.length > 0 ? occs.map((o) => o.slug) : undefined,
      senioritySlugs: sens.length > 0 ? sens.map((s) => s.slug) : undefined,
      technologySlugs: techs.length > 0 ? techs.map((t) => t.slug) : undefined,
      salaryCurrency: overrides.salCur ?? salaryCurrency,
      salaryMin: overrides.salMin !== undefined ? overrides.salMin : salaryMin,
      salaryMax: overrides.salMax !== undefined ? overrides.salMax : salaryMax,
      experienceMin: overrides.expMin !== undefined ? overrides.expMin : experienceMin,
      experienceMax: overrides.expMax !== undefined ? overrides.expMax : experienceMax,
      anyCompany: ac || undefined,
    };
  }

  // Filter callbacks
  function onRemoveKeyword(kw: string) {
    const next = keywords.filter((k) => k !== kw);
    setKeywords(next);
    persistFilters(buildFilters({ kw: next }));
  }
  function onAddLocation(loc: SelectedLocation) {
    const next = [...locations, loc];
    setLocations(next);
    persistFilters(buildFilters({ locs: next }));
  }
  function onRemoveLocation(id: number) {
    const next = locations.filter((l) => l.id !== id);
    setLocations(next);
    persistFilters(buildFilters({ locs: next }));
  }
  function onAddOccupation(occ: TaxonomyItem) {
    const next = [...occupations, occ];
    setOccupations(next);
    persistFilters(buildFilters({ occs: next }));
  }
  function onRemoveOccupation(id: number) {
    const next = occupations.filter((o) => o.id !== id);
    setOccupations(next);
    persistFilters(buildFilters({ occs: next }));
  }
  function onAddSeniority(sen: TaxonomyItem) {
    const next = [...seniorities, sen];
    setSeniorities(next);
    persistFilters(buildFilters({ sens: next }));
  }
  function onRemoveSeniority(id: number) {
    const next = seniorities.filter((s) => s.id !== id);
    setSeniorities(next);
    persistFilters(buildFilters({ sens: next }));
  }
  function onAddTechnology(tech: TaxonomyItem) {
    const next = [...technologies, tech];
    setTechnologies(next);
    persistFilters(buildFilters({ techs: next }));
  }
  function onRemoveTechnology(id: number) {
    const next = technologies.filter((t) => t.id !== id);
    setTechnologies(next);
    persistFilters(buildFilters({ techs: next }));
  }
  function onSalaryChange(currency: string, min: number | undefined, max: number | undefined) {
    setSalaryCurrency(currency);
    setSalaryMin(min);
    setSalaryMax(max);
    persistFilters(buildFilters({ salCur: currency, salMin: min, salMax: max }));
  }
  function onExperienceChange(min: number | undefined, max: number | undefined) {
    setExperienceMin(min);
    setExperienceMax(max);
    persistFilters(buildFilters({ expMin: min, expMax: max }));
  }
  function onClearAll() {
    setKeywords([]);
    setLocations([]);
    setOccupations([]);
    setSeniorities([]);
    setTechnologies([]);
    setSalaryMin(undefined);
    setSalaryMax(undefined);
    setExperienceMin(undefined);
    setExperienceMax(undefined);
    persistFilters({});
  }

  const hasFilters =
    keywords.length > 0 ||
    locations.length > 0 ||
    occupations.length > 0 ||
    seniorities.length > 0 ||
    technologies.length > 0 ||
    salaryMin != null ||
    salaryMax != null ||
    experienceMin != null ||
    experienceMax != null;

  return (
    <div className="space-y-6">
      {/* Back link */}
      <BackLink href={lp("/watchlists")}>
        {t({ id: "watchlists.view.back", comment: "Back to watchlists link", message: "Watchlists" })}
      </BackLink>

      {/* Configuration area */}
      <div className="space-y-4 rounded-lg border border-border-soft bg-surface p-4">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            {isOwner && editingTitle ? (
              <div className="flex items-center gap-2">
                <input
                  ref={titleInputRef}
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") saveTitle();
                    if (e.key === "Escape") {
                      setTitle(detail.title);
                      setEditingTitle(false);
                    }
                  }}
                  onBlur={saveTitle}
                  maxLength={100}
                  className="w-full rounded-md border border-border-soft bg-transparent px-2 py-1 text-xl font-semibold outline-none focus:border-primary"
                />
                {savingTitle && <Loader2 size={16} className="animate-spin text-muted" />}
              </div>
            ) : (
              <h1
                className={`group/title text-xl font-semibold ${isOwner ? "cursor-pointer rounded px-2 py-1 -mx-2 -my-1 transition-colors hover:bg-border-soft" : ""}`}
                onClick={isOwner ? () => setEditingTitle(true) : undefined}
                title={isOwner ? t({ id: "watchlists.view.editTitle", comment: "Tooltip for clicking to edit watchlist title", message: "Click to rename" }) : undefined}
              >
                {title}
                {isOwner && <Pencil size={14} className="ml-2 inline-block text-muted opacity-0 transition-opacity group-hover/title:opacity-100" />}
              </h1>
            )}
            <p className="mt-0.5 text-sm text-muted">
              @{detail.owner.displayUsername ?? detail.owner.username ?? detail.owner.name}
            </p>
          </div>
          <WatchlistActionBar
            watchlistId={detail.id}
            isOwner={isOwner}
            isPublic={detail.isPublic}
            alertsEnabled={detail.alertsEnabled}
            isPaidPlan={isPaidPlan}
            limitReached={limitReached}
          />
        </div>

        {/* Description */}
        {isOwner ? (
          editingDescription ? (
            <div className="flex items-start gap-2">
              <textarea
                ref={descriptionRef}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    saveDescription();
                  }
                  if (e.key === "Escape") {
                    setDescription(detail.description ?? "");
                    setEditingDescription(false);
                  }
                }}
                onBlur={saveDescription}
                maxLength={200}
                rows={2}
                className="w-full resize-none rounded-md border border-border-soft bg-transparent px-2 py-1 text-sm text-muted outline-none focus:border-primary"
                placeholder={t({ id: "watchlists.view.descriptionPlaceholder", comment: "Placeholder for watchlist description textarea", message: "Describe this watchlist..." })}
              />
              {savingDescription && <Loader2 size={14} className="mt-1.5 animate-spin text-muted" />}
            </div>
          ) : description ? (
            <p
              className="group/desc line-clamp-2 cursor-pointer rounded px-2 py-1 -mx-2 -my-1 text-sm text-muted transition-colors hover:bg-border-soft flex items-start gap-2"
              onClick={() => setEditingDescription(true)}
              title={t({ id: "watchlists.view.editDescription", comment: "Tooltip for clicking to edit watchlist description", message: "Click to edit description" })}
            >
              <span className="line-clamp-2">{description}</span>
              <Pencil size={12} className="mt-0.5 shrink-0 text-muted opacity-0 transition-opacity group-hover/desc:opacity-100" />
            </p>
          ) : (
            <button
              type="button"
              onClick={() => setEditingDescription(true)}
              className="flex items-center gap-1.5 cursor-pointer text-sm text-muted/60 transition-colors hover:text-muted"
            >
              <Pencil size={12} />
              {t({ id: "watchlists.view.addDescription", comment: "Link to add a description to the watchlist", message: "Add description" })}
            </button>
          )
        ) : description ? (
          <p className="text-sm text-muted">{description}</p>
        ) : null}

        {/* Companies */}
        <div className="space-y-2 !mt-6">
          {isOwner && (
            <div className="flex items-center gap-2">
              <button
                onClick={() => setCompanyModalOpen(true)}
                disabled={anyCompany}
                className="flex cursor-pointer items-center gap-2 rounded-md border border-dashed border-border-soft px-3 py-1.5 text-sm text-muted transition-colors hover:border-primary/30 hover:text-foreground disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Building2 size={14} className="shrink-0 text-muted" />
                {t({ id: "watchlists.view.addCompany", comment: "Button to open company search modal", message: "Company" })}
              </button>
              <button
                type="button"
                onClick={handleToggleAnyCompany}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors cursor-pointer ${
                  anyCompany
                    ? "bg-primary text-primary-contrast"
                    : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                }`}
              >
                {t({ id: "watchlists.view.anyCompany", comment: "Toggle to show jobs from all companies", message: "Any company" })}
              </button>
            </div>
          )}
          {!anyCompany && companies.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              {companies.map((c) => (
                <CompanyPill
                  key={c.id}
                  company={c}
                  onRemove={isOwner ? handleRemoveCompany : undefined}
                />
              ))}
              {isOwner && companies.length > 1 && (
                <button
                  onClick={handleClearAllCompanies}
                  className="cursor-pointer text-xs text-muted transition-colors hover:text-foreground"
                >
                  {t({ id: "watchlists.view.clearAllCompanies", comment: "Button to remove all companies from watchlist", message: "Clear all" })}
                </button>
              )}
            </div>
          )}
          {isOwner && (
            <CompanySearchModal
              open={companyModalOpen}
              onOpenChange={setCompanyModalOpen}
              selected={companies}
              onToggle={handleToggleCompany}
              onClearAll={handleClearAllCompanies}
              locale={locale}
              watchlistFilters={{
                keywords: keywords.length > 0 ? keywords : undefined,
                locationIds: locations.length > 0 ? locations.map((l) => l.id) : undefined,
                occupationIds: occupations.length > 0 ? occupations.map((o) => o.id) : undefined,
                seniorityIds: seniorities.length > 0 ? seniorities.map((s) => s.id) : undefined,
                technologyIds: technologies.length > 0 ? technologies.map((t) => t.id) : undefined,
                salaryMin,
                salaryMax,
                experienceMin,
                experienceMax,
                languages: languages.length > 0 ? languages : undefined,
              }}
            />
          )}
        </div>

        {/* Filters */}
        {isOwner ? (
          <div className="space-y-3">
            <AdvancedSearchPanel
              locale={locale}
              locations={locations}
              occupations={occupations}
              seniorities={seniorities}
              technologies={technologies}
              salaryCurrency={salaryCurrency}
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
                  <span key={`occ-${occ.id}`} className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary">
                    <Briefcase size={12} className="shrink-0" />
                    {occ.name}
                    <button onClick={() => onRemoveOccupation(occ.id)} className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"><X size={12} /></button>
                  </span>
                ))}
                {seniorities.map((sen) => (
                  <span key={`sen-${sen.id}`} className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary">
                    <BarChart3 size={12} className="shrink-0" />
                    {sen.name}
                    <button onClick={() => onRemoveSeniority(sen.id)} className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"><X size={12} /></button>
                  </span>
                ))}
                {technologies.map((tech) => (
                  <span key={`tech-${tech.id}`} className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary">
                    <Code2 size={12} className="shrink-0" />
                    {tech.name}
                    <button onClick={() => onRemoveTechnology(tech.id)} className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"><X size={12} /></button>
                  </span>
                ))}
                {(salaryMin != null || salaryMax != null) && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary">
                    <DollarSign size={12} className="shrink-0" />
                    {salaryMin != null && salaryMax != null
                      ? `${salaryCurrency} ${Math.round(salaryMin / 1000)}K – ${Math.round(salaryMax / 1000)}K`
                      : salaryMin != null ? `${salaryCurrency} ${Math.round(salaryMin / 1000)}K+` : `${salaryCurrency} ≤${Math.round(salaryMax! / 1000)}K`}
                    <button onClick={() => onSalaryChange(salaryCurrency, undefined, undefined)} className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"><X size={12} /></button>
                  </span>
                )}
                {(experienceMin != null || experienceMax != null) && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary">
                    <Clock size={12} className="shrink-0" />
                    {experienceMin != null && experienceMax != null
                      ? `${experienceMin}–${experienceMax}y`
                      : experienceMin != null ? `${experienceMin}y+` : `≤${experienceMax}y`}
                    <button onClick={() => onExperienceChange(undefined, undefined)} className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"><X size={12} /></button>
                  </span>
                )}
                {keywords.map((kw) => (
                  <span key={kw} className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary">
                    {kw}
                    <button onClick={() => onRemoveKeyword(kw)} className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"><X size={12} /></button>
                  </span>
                ))}
                {locations.map((loc) => (
                  <span key={loc.id} className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary">
                    <MapPin size={12} className="shrink-0" />
                    {loc.parentName && loc.type !== "country" && loc.type !== "macro" ? `${loc.name}, ${loc.parentName}` : loc.name}
                    <button onClick={() => onRemoveLocation(loc.id)} className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"><X size={12} /></button>
                  </span>
                ))}
                <button onClick={onClearAll} className="cursor-pointer text-xs text-muted transition-colors hover:text-foreground">
                  {t({ id: "search.filters.clearAll", comment: "Button to clear all active search filters", message: "Clear all" })}
                </button>
              </div>
            )}
          </div>
        ) : (
          <FilterPillsReadOnly
            filters={detail.filters}
            locations={resolvedLocations}
            occupations={resolvedOccupations}
            seniorities={resolvedSeniorities}
            technologies={resolvedTechnologies}
          />
        )}
      </div>

      {/* Job results. WatchlistJobList owns the "Showing jobs ... ·
          N active · M in the last year" row internally so it stays
          inside the left flex column alongside the postings list,
          not stacked above the detail panel. */}
      <WatchlistJobList
        filters={{
          companyIds: anyCompany ? [] : companies.map((c) => c.id),
          anyCompany,
          keywords: keywords.length > 0 ? keywords : undefined,
          locationIds: locations.length > 0 ? locations.map((l) => l.id) : undefined,
          occupationIds: occupations.length > 0 ? occupations.map((o) => o.id) : undefined,
          seniorityIds: seniorities.length > 0 ? seniorities.map((s) => s.id) : undefined,
          technologyIds: technologies.length > 0 ? technologies.map((t) => t.id) : undefined,
          salaryMin,
          salaryMax,
          experienceMin,
          experienceMax,
          languages: languages.length > 0 ? languages : undefined,
        }}
        initialPostings={initialPostings}
        initialTotal={initialTotal}
        yearTotal={yearTotal}
        jobLanguages={jobLanguages}
        locale={locale}
      />
    </div>
  );
}
