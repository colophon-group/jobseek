import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// ── Mocks ────────────────────────────────────────────────────────────
//
// `SearchBar` has a heavy fan-in: server actions, the typeahead runner,
// the lingui macro, and the navigation hooks. Each of those is mocked
// in isolation so the suite exercises only the dropdown wiring around
// the synthetic "Request <query>" entry (issue #2807).

// Lingui's `useLingui` macro normally needs the babel transform (see
// apps/web/babel.config.json). Tests run under Vitest + esbuild without
// that transform, so substitute a runtime-friendly hook that returns a
// `t` which simply renders the descriptor's `message`.
vi.mock("@lingui/react/macro", () => ({
  useLingui: () => ({
    t: (input: unknown) => {
      if (typeof input === "string") return input;
      const desc = input as { message?: string; id?: string };
      return desc.message ?? desc.id ?? "";
    },
  }),
}));

const pushMock = vi.fn();
const currentSearchParams = new URLSearchParams();
let currentPathname = "/en";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: pushMock, refresh: () => {} }),
  useSearchParams: () => currentSearchParams,
  usePathname: () => currentPathname,
  useParams: () => ({ lang: "en" }),
}));

// Server actions / typeahead runners — controlled per test.
const suggestCompaniesMock = vi.fn();
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
  runSuggestLocations: vi.fn(async () => []),
  runSuggestOccupations: vi.fn(async () => []),
  runSuggestSeniorities: vi.fn(async () => []),
  runSuggestTechnologies: vi.fn(async () => []),
}));

// `parseSearchFilters` is a server action — only used on the keyword
// fast-path so a noop is enough.
vi.mock("@/lib/actions/search-input", () => ({
  parseSearchFilters: vi.fn(async () => ({
    keywords: [],
    locations: [],
    occupations: [],
    seniorities: [],
    technologies: [],
  })),
}));

// `server-only` is imported transitively through company.ts; neutralise
// the gate so happy-dom can load it.
vi.mock("server-only", () => ({}));

import { SearchBar } from "../search-bar";

beforeEach(() => {
  pushMock.mockReset();
  suggestCompaniesMock.mockReset();
  currentPathname = "/en";
});

afterEach(() => {
  vi.clearAllTimers();
});

async function typeInBar(value: string) {
  const input = screen.getByRole("combobox");
  await userEvent.type(input, value);
  // The dropdown's debounce is 200ms; advance both fake timers (if used)
  // and real microtasks so the pending fetches settle before assertions.
  await new Promise((r) => setTimeout(r, 250));
}

describe("SearchBar — synthetic 'Request <query>' dropdown row (#2807)", () => {
  it("Type 'Anthropic' → dropdown shows 'Request \"Anthropic\"' item", async () => {
    suggestCompaniesMock.mockResolvedValue([]);
    render(<SearchBar />);
    await act(async () => {
      await typeInBar("Anthropic");
    });

    const row = await screen.findByTestId("search-bar-request-item");
    expect(row).toBeTruthy();
    expect(row.textContent).toContain("Anthropic");
    // Distinct visual: opt to a non-default icon container — assert the
    // request row is rendered with role=option so it's part of the
    // listbox a11y tree.
    expect(row.getAttribute("role")).toBe("option");
  });

  it("Type 'Stripe' (already in catalog) → dropdown shows the real Stripe entry, no Request item", async () => {
    suggestCompaniesMock.mockResolvedValue([
      { id: "co_stripe", name: "Stripe", slug: "stripe", icon: null },
    ]);
    render(<SearchBar />);
    await act(async () => {
      await typeInBar("Stripe");
    });

    expect(await screen.findByText("Stripe")).toBeTruthy();
    expect(screen.queryByTestId("search-bar-request-item")).toBeNull();
  });

  it("Type '' (empty) → no Request item", async () => {
    suggestCompaniesMock.mockResolvedValue([]);
    render(<SearchBar />);
    // Don't type anything — dropdown should not even open, and certainly
    // no Request row.
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("search-bar-request-item")).toBeNull();
  });

  it("Click the Request item → navigates to /en/companies/request?name=Anthropic", async () => {
    suggestCompaniesMock.mockResolvedValue([]);
    render(<SearchBar />);
    await act(async () => {
      await typeInBar("Anthropic");
    });

    const row = await screen.findByTestId("search-bar-request-item");
    // Component listens on onMouseDown (preserves focus); fire that.
    await act(async () => {
      await userEvent.pointer({ keys: "[MouseLeft>]", target: row });
    });

    expect(pushMock).toHaveBeenCalledTimes(1);
    expect(pushMock).toHaveBeenCalledWith(
      "/en/companies/request?name=Anthropic",
    );
  });

  it("Click the Request item with a multi-word query → URL-encodes the name", async () => {
    suggestCompaniesMock.mockResolvedValue([]);
    render(<SearchBar />);
    await act(async () => {
      await typeInBar("Some Company & Co.");
    });

    const row = await screen.findByTestId("search-bar-request-item");
    await act(async () => {
      await userEvent.pointer({ keys: "[MouseLeft>]", target: row });
    });

    expect(pushMock).toHaveBeenCalledWith(
      `/en/companies/request?name=${encodeURIComponent("Some Company & Co.")}`,
    );
  });

  it("Keyboard: arrow-down to the Request item, Enter activates it", async () => {
    suggestCompaniesMock.mockResolvedValue([]);
    render(<SearchBar />);
    await act(async () => {
      await typeInBar("Anthropic");
    });

    // The dropdown should have at least the keyword row + the request
    // row at this point (since suggest* mocks return empty arrays). The
    // request row is always last.
    const input = screen.getByRole("combobox");
    // Walk activeIndex down until the request row is highlighted. We
    // press ArrowDown enough times to definitely reach the bottom — the
    // handler clamps at the last index.
    for (let i = 0; i < 10; i++) {
      await act(async () => {
        await userEvent.type(input, "{ArrowDown}");
      });
    }

    const row = await screen.findByTestId("search-bar-request-item");
    expect(row.getAttribute("aria-selected")).toBe("true");

    await act(async () => {
      await userEvent.type(input, "{Enter}");
    });

    expect(pushMock).toHaveBeenCalledWith(
      "/en/companies/request?name=Anthropic",
    );
  });

  it("Does NOT show the Request item when scoped to a company route", async () => {
    suggestCompaniesMock.mockResolvedValue([]);
    currentPathname = "/en/company/stripe";
    render(<SearchBar />);
    await act(async () => {
      await typeInBar("Anthropic");
    });

    expect(screen.queryByTestId("search-bar-request-item")).toBeNull();
  });
});
