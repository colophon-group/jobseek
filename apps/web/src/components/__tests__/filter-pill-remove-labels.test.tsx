import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

vi.mock("server-only", () => ({}));

import "@/test-utils/lingui-mock";

vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    href,
    onClick,
    children,
    prefetch: _prefetch,
    ...rest
  }: {
    href: string;
    onClick?: (e: React.MouseEvent) => void;
    children: React.ReactNode;
    prefetch?: boolean;
  }) => (
    <a href={href} onClick={onClick} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/en/explore",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ lang: "en" }),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (p: string) => p,
}));

import { SearchToolbar } from "../search/search-toolbar";
import { FilterPillsReadOnly } from "../search/filter-pills-readonly";
import { WatchlistFilterEditor } from "../watchlist/watchlist-filter-editor";

describe("filter pill remove buttons", () => {
  it("names every active SearchToolbar remove button", () => {
    render(
      <SearchToolbar
        locale="en"
        keywords={["typescript"]}
        locations={[{ id: 1, slug: "berlin", name: "Berlin", type: "city", parentName: "Germany" }]}
        occupations={[{ id: 2, slug: "engineering", name: "Engineering" }]}
        seniorities={[{ id: 3, slug: "senior", name: "Senior" }]}
        technologies={[{ id: 4, slug: "react", name: "React" }]}
        employmentTypes={["full_time"]}
        workMode={["remote"]}
        salaryCurrency="EUR"
        salaryMin={100000}
        salaryMax={150000}
        experienceMin={3}
        experienceMax={7}
        jobLanguages={[]}
        onRemoveKeyword={() => {}}
        onAddLocation={() => {}}
        onRemoveLocation={() => {}}
        onAddOccupation={() => {}}
        onRemoveOccupation={() => {}}
        onAddSeniority={() => {}}
        onRemoveSeniority={() => {}}
        onAddTechnology={() => {}}
        onRemoveTechnology={() => {}}
        onToggleEmploymentType={() => {}}
        onToggleWorkMode={() => {}}
        onSalaryChange={() => {}}
        onExperienceChange={() => {}}
        onClearAll={() => {}}
        onSubmitSearch={() => {}}
      />,
    );

    for (const name of [
      "Remove Engineering filter",
      "Remove Senior filter",
      "Remove React filter",
      "Remove full time filter",
      "Remove Remote filter",
      "Remove salary filter",
      "Remove experience filter",
      "Remove keyword typescript",
      "Remove location Berlin, Germany",
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeNull();
    }
  });

  it("names every active WatchlistFilterEditor remove button", () => {
    render(
      <WatchlistFilterEditor
        filters={{
          keywords: ["backend"],
          locationSlugs: ["zurich"],
          occupationSlugs: ["software-engineer"],
          senioritySlugs: ["senior"],
          technologySlugs: ["python"],
          employmentType: ["full_time"],
          workMode: ["hybrid"],
          salaryCurrency: "CHF",
          salaryMin: 120000,
          salaryMax: 180000,
          experienceMin: 4,
          experienceMax: 8,
        }}
        onChange={() => {}}
      />,
    );

    for (const name of [
      "Remove backend filter",
      "Remove zurich filter",
      "Remove software-engineer filter",
      "Remove senior filter",
      "Remove python filter",
      "Remove Full-time filter",
      "Remove Hybrid filter",
      "Remove salary filter",
      "Remove experience filter",
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeNull();
    }
  });

  it("renders shared watchlist pills read-only when removal callbacks are absent", () => {
    render(
      <FilterPillsReadOnly
        filters={{
          keywords: ["backend"],
          salaryCurrency: "CHF",
          salaryMin: 120000,
        }}
        locations={[{ id: 1, slug: "zurich", name: "Zurich", type: "city", parentName: "Switzerland" }]}
      />,
    );

    expect(screen.queryByText("backend")).not.toBeNull();
    expect(screen.queryByText("Zurich, Switzerland")).not.toBeNull();
    expect(screen.queryByText("CHF 120K+")).not.toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders shared watchlist pills with owner removal callbacks", () => {
    const onRemoveKeyword = vi.fn();
    const onRemoveLocation = vi.fn();
    const onRemoveOccupation = vi.fn();
    const onRemoveSeniority = vi.fn();
    const onRemoveTechnology = vi.fn();
    const onToggleEmploymentType = vi.fn();
    const onToggleWorkMode = vi.fn();
    const onRemoveSalary = vi.fn();
    const onRemoveExperience = vi.fn();
    const onClearAll = vi.fn();

    render(
      <FilterPillsReadOnly
        filters={{
          keywords: ["backend"],
          salaryCurrency: "CHF",
          salaryMin: 120000,
          salaryMax: 180000,
          experienceMin: 4,
          experienceMax: 8,
        }}
        locations={[{ id: 1, slug: "zurich", name: "Zurich", type: "city", parentName: "Switzerland" }]}
        occupations={[{ id: 2, slug: "software-engineer", name: "Software Engineer" }]}
        seniorities={[{ id: 3, slug: "senior", name: "Senior" }]}
        technologies={[{ id: 4, slug: "python", name: "Python" }]}
        employmentType={["full_time"]}
        workMode={["hybrid"]}
        onRemoveKeyword={onRemoveKeyword}
        onRemoveLocation={onRemoveLocation}
        onRemoveOccupation={onRemoveOccupation}
        onRemoveSeniority={onRemoveSeniority}
        onRemoveTechnology={onRemoveTechnology}
        onToggleEmploymentType={onToggleEmploymentType}
        onToggleWorkMode={onToggleWorkMode}
        onRemoveSalary={onRemoveSalary}
        onRemoveExperience={onRemoveExperience}
        onClearAll={onClearAll}
      />,
    );

    for (const name of [
      "Remove keyword backend",
      "Remove location Zurich, Switzerland",
      "Remove Software Engineer filter",
      "Remove Senior filter",
      "Remove Python filter",
      "Remove full time filter",
      "Remove Hybrid filter",
      "Remove salary filter",
      "Remove experience filter",
      "Clear all",
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeNull();
    }

    fireEvent.click(screen.getByRole("button", { name: "Remove keyword backend" }));
    expect(onRemoveKeyword).toHaveBeenCalledWith("backend");

    fireEvent.click(screen.getByRole("button", { name: "Remove location Zurich, Switzerland" }));
    expect(onRemoveLocation).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }));

    fireEvent.click(screen.getByRole("button", { name: "Remove salary filter" }));
    expect(onRemoveSalary).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(onClearAll).toHaveBeenCalledOnce();
  });
});
