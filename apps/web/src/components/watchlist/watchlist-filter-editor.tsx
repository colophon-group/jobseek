"use client";

import { useState } from "react";
import { X, Plus, Check, Loader2, MapPin, Briefcase, Award, Cpu, DollarSign, Clock, Home, CalendarDays } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import type { WatchlistFilters } from "@/lib/actions/watchlists";
import type { WorkMode } from "@/lib/search/types";

// Closed sets for the two enum-shaped filters added in issue #3037.
// Defining them client-side mirrors the Typesense canonical values and
// keeps the editor decoupled from server-only modules.
//
// The translated label is obtained via {@link useEnumLabels} below; the
// Lingui babel macro requires literal `id` strings so we can't loop and
// `t({ id: opt.labelKey, … })`. Instead the hook holds an explicit
// `switch` mapping value -> macro call.
const WORK_MODE_VALUES = ["onsite", "hybrid", "remote"] as const satisfies readonly WorkMode[];

const EMPLOYMENT_TYPE_VALUES = [
  "full_time",
  "part_time",
  "contract",
  "internship",
  "temporary",
  "volunteer",
] as const;

// Lingui macro requires literal `id` strings, so the enum -> label map
// can't be a dynamic loop. Each branch below is rewritten by Babel into a
// `i18n._({ id, message })` call with the static id baked in.
type TFn = ReturnType<typeof useLingui>["t"];

function workModeLabel(t: TFn, value: WorkMode): string {
  switch (value) {
    case "onsite":
      return t({ id: "search.workMode.onsite", comment: "Work mode label", message: "On-site" });
    case "hybrid":
      return t({ id: "search.workMode.hybrid", comment: "Work mode label", message: "Hybrid" });
    case "remote":
      return t({ id: "search.workMode.remote", comment: "Work mode label", message: "Remote" });
  }
}

function employmentTypeLabel(t: TFn, value: string): string {
  switch (value) {
    case "full_time":
      return t({ id: "search.employmentType.fullTime", comment: "Employment type label", message: "Full-time" });
    case "part_time":
      return t({ id: "search.employmentType.partTime", comment: "Employment type label", message: "Part-time" });
    case "contract":
      return t({ id: "search.employmentType.contract", comment: "Employment type label", message: "Contract" });
    case "internship":
      return t({ id: "search.employmentType.internship", comment: "Employment type label", message: "Internship" });
    case "temporary":
      return t({ id: "search.employmentType.temporary", comment: "Employment type label", message: "Temporary" });
    case "volunteer":
      return t({ id: "search.employmentType.volunteer", comment: "Employment type label", message: "Volunteer" });
    default:
      return value;
  }
}

function FilterPill({
  icon,
  label,
  onRemove,
  removeLabel,
}: {
  icon?: React.ReactNode;
  label: string;
  onRemove: () => void;
  removeLabel: string;
}) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary">
      {icon && <span className="shrink-0">{icon}</span>}
      {label}
      <button
        type="button"
        onClick={onRemove}
        className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
        aria-label={removeLabel}
      >
        <X size={12} aria-hidden="true" />
      </button>
    </span>
  );
}

type AddingType = "keyword" | "location" | "occupation" | "seniority" | "technology" | "workMode" | "employmentType" | null;

export function WatchlistFilterEditor({
  filters,
  onChange,
  saving,
}: {
  filters: WatchlistFilters;
  onChange: (filters: WatchlistFilters) => void;
  saving?: boolean;
}) {
  const { t } = useLingui();
  const [adding, setAdding] = useState<AddingType>(null);
  const [inputValue, setInputValue] = useState("");

  function removeKeyword(kw: string) {
    onChange({
      ...filters,
      keywords: (filters.keywords ?? []).filter((k) => k !== kw),
    });
  }
  function removeLocation(slug: string) {
    onChange({
      ...filters,
      locationSlugs: (filters.locationSlugs ?? []).filter((s) => s !== slug),
    });
  }
  function removeOccupation(slug: string) {
    onChange({
      ...filters,
      occupationSlugs: (filters.occupationSlugs ?? []).filter((s) => s !== slug),
    });
  }
  function removeSeniority(slug: string) {
    onChange({
      ...filters,
      senioritySlugs: (filters.senioritySlugs ?? []).filter((s) => s !== slug),
    });
  }
  function removeTechnology(slug: string) {
    onChange({
      ...filters,
      technologySlugs: (filters.technologySlugs ?? []).filter((s) => s !== slug),
    });
  }
  function toggleWorkMode(mode: WorkMode) {
    const cur = filters.workMode ?? [];
    const next = cur.includes(mode) ? cur.filter((m) => m !== mode) : [...cur, mode];
    onChange({ ...filters, workMode: next.length > 0 ? next : undefined });
  }
  function toggleEmploymentType(type: string) {
    const cur = filters.employmentType ?? [];
    const next = cur.includes(type) ? cur.filter((t) => t !== type) : [...cur, type];
    onChange({ ...filters, employmentType: next.length > 0 ? next : undefined });
  }
  function removeSalary() {
    onChange({
      ...filters,
      salaryMin: undefined,
      salaryMax: undefined,
      salaryCurrency: undefined,
    });
  }
  function removeExperience() {
    onChange({
      ...filters,
      experienceMin: undefined,
      experienceMax: undefined,
    });
  }

  function submitInput() {
    // workMode / employmentType use chip-toggle UI, not the free-text
    // input — short-circuit here so an accidental Enter on the input
    // while one of those tabs is active doesn't append the raw string
    // as a slug.
    if (adding === "workMode" || adding === "employmentType") {
      setInputValue("");
      setAdding(null);
      return;
    }
    const val = inputValue.trim();
    if (!val || !adding) return;

    const updated = { ...filters };
    switch (adding) {
      case "keyword":
        updated.keywords = [...(filters.keywords ?? []), val];
        break;
      case "location":
        updated.locationSlugs = [...(filters.locationSlugs ?? []), val];
        break;
      case "occupation":
        updated.occupationSlugs = [...(filters.occupationSlugs ?? []), val];
        break;
      case "seniority":
        updated.senioritySlugs = [...(filters.senioritySlugs ?? []), val];
        break;
      case "technology":
        updated.technologySlugs = [...(filters.technologySlugs ?? []), val];
        break;
    }
    onChange(updated);
    setInputValue("");
    setAdding(null);
  }

  const hasAnyFilter =
    (filters.keywords?.length ?? 0) > 0 ||
    (filters.locationSlugs?.length ?? 0) > 0 ||
    (filters.occupationSlugs?.length ?? 0) > 0 ||
    (filters.senioritySlugs?.length ?? 0) > 0 ||
    (filters.technologySlugs?.length ?? 0) > 0 ||
    (filters.workMode?.length ?? 0) > 0 ||
    (filters.employmentType?.length ?? 0) > 0 ||
    filters.salaryMin != null ||
    filters.salaryMax != null ||
    filters.experienceMin != null ||
    filters.experienceMax != null;

  const filterOptions: { type: AddingType; icon: React.ReactNode; label: string }[] = [
    { type: "keyword", icon: null, label: t({ id: "watchlists.filters.keyword", comment: "Add keyword filter", message: "Keyword" }) },
    { type: "location", icon: <MapPin size={12} />, label: t({ id: "watchlists.filters.location", comment: "Add location filter", message: "Location" }) },
    { type: "occupation", icon: <Briefcase size={12} />, label: t({ id: "watchlists.filters.occupation", comment: "Add occupation filter", message: "Occupation" }) },
    { type: "seniority", icon: <Award size={12} />, label: t({ id: "watchlists.filters.seniority", comment: "Add seniority filter", message: "Seniority" }) },
    { type: "technology", icon: <Cpu size={12} />, label: t({ id: "watchlists.filters.technology", comment: "Add technology filter", message: "Technology" }) },
    { type: "employmentType", icon: <CalendarDays size={12} />, label: t({ id: "watchlists.filters.employmentType", comment: "Add employment type filter", message: "Type" }) },
    { type: "workMode", icon: <Home size={12} />, label: t({ id: "watchlists.filters.workMode", comment: "Add work-mode (onsite/hybrid/remote) filter", message: "Work mode" }) },
  ];

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">
          <Trans id="watchlists.view.filters" comment="Section title for filters in watchlist view">
            Filters
          </Trans>
        </h2>
        {saving && <Loader2 size={12} className="animate-spin text-muted" />}
      </div>

      {/* Existing filter pills */}
      <div className="flex flex-wrap items-center gap-2">
        {filters.keywords?.map((kw) => (
          <FilterPill
            key={`kw-${kw}`}
            label={kw}
            onRemove={() => removeKeyword(kw)}
            removeLabel={t({ id: "watchlists.filters.removeFilter", comment: "Aria label for remove-filter X button on a watchlist pill; {name} is the filter value", message: `Remove ${kw} filter` })}
          />
        ))}
        {filters.locationSlugs?.map((slug) => (
          <FilterPill
            key={`loc-${slug}`}
            icon={<MapPin size={12} />}
            label={slug}
            onRemove={() => removeLocation(slug)}
            removeLabel={t({ id: "watchlists.filters.removeFilter", comment: "Aria label for remove-filter X button on a watchlist pill; {name} is the filter value", message: `Remove ${slug} filter` })}
          />
        ))}
        {filters.occupationSlugs?.map((slug) => (
          <FilterPill
            key={`occ-${slug}`}
            icon={<Briefcase size={12} />}
            label={slug}
            onRemove={() => removeOccupation(slug)}
            removeLabel={t({ id: "watchlists.filters.removeFilter", comment: "Aria label for remove-filter X button on a watchlist pill; {name} is the filter value", message: `Remove ${slug} filter` })}
          />
        ))}
        {filters.senioritySlugs?.map((slug) => (
          <FilterPill
            key={`sen-${slug}`}
            icon={<Award size={12} />}
            label={slug}
            onRemove={() => removeSeniority(slug)}
            removeLabel={t({ id: "watchlists.filters.removeFilter", comment: "Aria label for remove-filter X button on a watchlist pill; {name} is the filter value", message: `Remove ${slug} filter` })}
          />
        ))}
        {filters.technologySlugs?.map((slug) => (
          <FilterPill
            key={`tech-${slug}`}
            icon={<Cpu size={12} />}
            label={slug}
            onRemove={() => removeTechnology(slug)}
            removeLabel={t({ id: "watchlists.filters.removeFilter", comment: "Aria label for remove-filter X button on a watchlist pill; {name} is the filter value", message: `Remove ${slug} filter` })}
          />
        ))}
        {filters.employmentType?.map((type) => {
          const label = employmentTypeLabel(t, type);
          return (
            <FilterPill
              key={`et-${type}`}
              icon={<CalendarDays size={12} />}
              label={label}
              onRemove={() => toggleEmploymentType(type)}
              removeLabel={t({ id: "watchlists.filters.removeFilter", comment: "Aria label for remove-filter X button on a watchlist pill; {name} is the filter value", message: `Remove ${label} filter` })}
            />
          );
        })}
        {filters.workMode?.map((mode) => {
          const label = workModeLabel(t, mode as WorkMode);
          return (
            <FilterPill
              key={`wm-${mode}`}
              icon={<Home size={12} />}
              label={label}
              onRemove={() => toggleWorkMode(mode as WorkMode)}
              removeLabel={t({ id: "watchlists.filters.removeFilter", comment: "Aria label for remove-filter X button on a watchlist pill; {name} is the filter value", message: `Remove ${label} filter` })}
            />
          );
        })}
        {(filters.salaryMin != null || filters.salaryMax != null) && (
          <FilterPill
            icon={<DollarSign size={12} />}
            label={[
              filters.salaryMin ?? "",
              "–",
              filters.salaryMax ?? "",
              filters.salaryCurrency ?? "",
            ].filter(Boolean).join(" ")}
            onRemove={removeSalary}
            removeLabel={t({ id: "watchlists.filters.removeSalary", comment: "Aria label for the X button that clears the salary-range watchlist filter", message: "Remove salary filter" })}
          />
        )}
        {(filters.experienceMin != null || filters.experienceMax != null) && (
          <FilterPill
            icon={<Clock size={12} />}
            label={[
              filters.experienceMin ?? "",
              "–",
              filters.experienceMax ?? "",
              "yrs",
            ].filter(Boolean).join(" ")}
            onRemove={removeExperience}
            removeLabel={t({ id: "watchlists.filters.removeExperience", comment: "Aria label for the X button that clears the experience-range watchlist filter", message: "Remove experience filter" })}
          />
        )}

        {/* Add filter dropdown */}
        {adding === null && (
          <div className="relative inline-block">
            <button
              type="button"
              onClick={() => setAdding("keyword")}
              className="inline-flex items-center gap-1 rounded-full border border-dashed border-border-soft px-2.5 py-1 text-sm text-muted transition-colors hover:border-primary/30 hover:text-foreground cursor-pointer"
            >
              <Plus size={12} />
              <Trans id="watchlists.filters.add" comment="Button to add a filter to watchlist">
                Filter
              </Trans>
            </button>
          </div>
        )}
      </div>

      {/* Inline add UI */}
      {adding !== null && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {/* Type selector tabs */}
          <div className="flex rounded-md border border-border-soft">
            {filterOptions.map((opt) => (
              <button
                key={opt.type}
                type="button"
                onClick={() => { setAdding(opt.type); setInputValue(""); }}
                className={`flex items-center gap-1 px-2 py-1 text-xs font-medium transition-colors cursor-pointer first:rounded-l-md last:rounded-r-md ${
                  adding === opt.type
                    ? "bg-primary text-primary-contrast"
                    : "text-muted hover:text-foreground hover:bg-border-soft"
                }`}
              >
                {opt.icon}
                {opt.label}
              </button>
            ))}
          </div>

          {/* Value input — chip-toggle for enum filters, text for slugs */}
          {adding === "workMode" || adding === "employmentType" ? (
            <div className="flex flex-wrap items-center gap-1">
              {(adding === "workMode"
                ? WORK_MODE_VALUES.map((v) => ({ value: v as string }))
                : EMPLOYMENT_TYPE_VALUES.map((v) => ({ value: v as string }))
              ).map((opt) => {
                const isActive =
                  adding === "workMode"
                    ? (filters.workMode ?? []).includes(opt.value as WorkMode)
                    : (filters.employmentType ?? []).includes(opt.value);
                const onClick =
                  adding === "workMode"
                    ? () => toggleWorkMode(opt.value as WorkMode)
                    : () => toggleEmploymentType(opt.value);
                const label =
                  adding === "workMode"
                    ? workModeLabel(t, opt.value as WorkMode)
                    : employmentTypeLabel(t, opt.value);
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={onClick}
                    className={`rounded-full border px-2.5 py-1 text-xs transition-colors cursor-pointer ${
                      isActive
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                    }`}
                  >
                    {label}
                  </button>
                );
              })}
              <button
                type="button"
                onClick={() => { setAdding(null); setInputValue(""); }}
                className="rounded-md p-1 text-muted transition-colors hover:text-foreground cursor-pointer"
                aria-label={t({ id: "watchlists.filters.cancel", comment: "Aria label for the X button that cancels the inline add-filter UI", message: "Cancel" })}
              >
                <X size={14} aria-hidden="true" />
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-1">
              <input
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") submitInput();
                  if (e.key === "Escape") { setAdding(null); setInputValue(""); }
                }}
                placeholder={t({
                  id: "watchlists.filters.inputPlaceholder",
                  comment: "Placeholder for filter value input",
                  message: "Type slug and press Enter",
                })}
                autoFocus
                className="w-40 rounded-md border border-border-soft bg-transparent px-2 py-1 text-sm outline-none focus:border-primary placeholder:text-muted"
              />
              <button
                type="button"
                onClick={submitInput}
                disabled={!inputValue.trim()}
                className="rounded-md p-1 text-muted transition-colors hover:text-foreground disabled:opacity-40 cursor-pointer"
                aria-label={t({ id: "watchlists.filters.confirmAdd", comment: "Aria label for the checkmark button that confirms adding a filter", message: "Add filter" })}
              >
                <Check size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={() => { setAdding(null); setInputValue(""); }}
                className="rounded-md p-1 text-muted transition-colors hover:text-foreground cursor-pointer"
                aria-label={t({ id: "watchlists.filters.cancel", comment: "Aria label for the X button that cancels the inline add-filter UI", message: "Cancel" })}
              >
                <X size={14} aria-hidden="true" />
              </button>
            </div>
          )}
        </div>
      )}

      {!hasAnyFilter && adding === null && (
        <p className="mt-1 text-xs text-muted">
          <Trans id="watchlists.filters.empty" comment="Empty state when no filters are set">
            No filters set. Add filters to narrow job results.
          </Trans>
        </p>
      )}
    </div>
  );
}
