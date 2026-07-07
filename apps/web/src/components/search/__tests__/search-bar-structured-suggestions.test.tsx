import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import "@/test-utils/lingui-mock";

const pushMock = vi.fn();
const currentSearchParams = new URLSearchParams();
let currentPathname = "/en";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: pushMock, refresh: () => {} }),
  useSearchParams: () => currentSearchParams,
  usePathname: () => currentPathname,
  useParams: () => ({ lang: "en" }),
}));

const suggestCompaniesMock = vi.fn();
const suggestLocationsMock = vi.fn();
const suggestOccupationsMock = vi.fn();
const suggestSenioritiesMock = vi.fn();
const suggestTechnologiesMock = vi.fn();

vi.mock("@/lib/actions/company", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/actions/company")>(
      "@/lib/actions/company",
    );
  return {
    ...actual,
    suggestCompanies: (...args: unknown[]) => suggestCompaniesMock(...args),
  };
});

vi.mock("@/lib/search/typeahead-runner", () => ({
  runSuggestLocations: (...args: unknown[]) => suggestLocationsMock(...args),
  runSuggestOccupations: (...args: unknown[]) => suggestOccupationsMock(...args),
  runSuggestSeniorities: (...args: unknown[]) => suggestSenioritiesMock(...args),
  runSuggestTechnologies: (...args: unknown[]) => suggestTechnologiesMock(...args),
}));

vi.mock("@/lib/actions/search-input", () => ({
  parseSearchFilters: vi.fn(async () => ({
    keywords: [],
    locations: [],
    occupations: [],
    seniorities: [],
    technologies: [],
  })),
}));

vi.mock("server-only", () => ({}));

import { SearchBar } from "../search-bar";

beforeEach(() => {
  pushMock.mockReset();
  currentPathname = "/en";
  suggestCompaniesMock.mockResolvedValue([]);
  suggestLocationsMock.mockResolvedValue([]);
  suggestOccupationsMock.mockResolvedValue([]);
  suggestSenioritiesMock.mockResolvedValue([]);
  suggestTechnologiesMock.mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllTimers();
});

async function typeInBar(value: string) {
  const input = screen.getByRole("combobox");
  await userEvent.type(input, value);
  await new Promise((r) => setTimeout(r, 250));
}

describe("SearchBar structured suggestion sections (#3082)", () => {
  it("renders all structured sections through the shared option row", async () => {
    suggestCompaniesMock.mockResolvedValue([
      { id: "co_acme", name: "Acme", slug: "acme", icon: null },
    ]);
    suggestLocationsMock.mockResolvedValue([
      {
        id: 10,
        slug: "zurich",
        name: "Zurich",
        type: "city",
        parentName: "Switzerland",
      },
    ]);
    suggestOccupationsMock.mockResolvedValue([
      { id: 20, slug: "software-engineer", name: "Software Engineer" },
    ]);
    suggestSenioritiesMock.mockResolvedValue([
      { id: 30, slug: "senior", name: "Senior" },
    ]);
    suggestTechnologiesMock.mockResolvedValue([
      { id: 40, slug: "react", name: "React" },
    ]);

    const onAddOccupation = vi.fn();
    render(<SearchBar onAddOccupation={onAddOccupation} />);

    await act(async () => {
      await typeInBar("eng");
    });

    expect(await screen.findByText("Roles")).toBeTruthy();
    expect(screen.getByText("Level")).toBeTruthy();
    expect(screen.getByText("Technologies")).toBeTruthy();
    expect(screen.getByText("Locations")).toBeTruthy();
    expect(screen.getByText("Companies")).toBeTruthy();
    expect(screen.getByText("Software Engineer")).toBeTruthy();
    expect(screen.getByText("Senior")).toBeTruthy();
    expect(screen.getByText("React")).toBeTruthy();
    expect(screen.getByText("Zurich")).toBeTruthy();
    expect(screen.getByText(", Switzerland")).toBeTruthy();
    expect(screen.getByText("Acme")).toBeTruthy();

    const row = screen.getByText("Software Engineer").closest("[role=option]");
    expect(row).not.toBeNull();
    fireEvent.mouseDown(row!);

    expect(onAddOccupation).toHaveBeenCalledWith({
      id: 20,
      slug: "software-engineer",
      name: "Software Engineer",
    });
  });

  it("keeps work-mode suggestion selection wired through the shared row", async () => {
    const onAddWorkMode = vi.fn();
    render(<SearchBar onAddWorkMode={onAddWorkMode} />);

    await act(async () => {
      await typeInBar("wfh");
    });

    const row = await screen.findByTestId("search-bar-workmode-remote");
    fireEvent.mouseDown(row);

    expect(onAddWorkMode).toHaveBeenCalledWith("remote");
  });
});
