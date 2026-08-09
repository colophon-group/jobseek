import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import "@/test-utils/lingui-mock";

// ── Mocks ────────────────────────────────────────────────────────────
//
// `GeneralSettings` is a heavy client component but the regression for
// #2988 lives entirely in `handleLocaleSwitch` — the click handler on
// each Language-section button. The test renders the real component
// with the smallest mock surface that lets the click path execute, then
// asserts the two side effects that close the bug:
//
//   1. `document.cookie` gains `NEXT_LOCALE=<new locale>`. Without the
//      cookie, `LocaleGuard` (mounted in `[lang]/layout.tsx`) has no
//      signal to redirect history-based navigation back to /explore on
//      the new locale.
//   2. `router.push` fires for the same path with the new `[lang]`
//      prefix — that part already worked pre-fix, but is asserted here
//      so a future refactor of the click handler can't silently
//      regress the navigation step while preserving the cookie.
//
// We deliberately avoid asserting on `localStorage` writes or
// `updatePreferences` calls — those exist for unrelated reasons and
// belong to other tests.

const pushMock = vi.fn();
const refreshMock = vi.fn();
let currentPathname = "/en/settings";
const currentSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: pushMock,
    replace: vi.fn(),
    refresh: refreshMock,
  }),
  usePathname: () => currentPathname,
  useSearchParams: () => currentSearchParams,
  useParams: () => ({ lang: "en" }),
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "dark", setTheme: vi.fn(), resolvedTheme: "dark" }),
}));

const updatePreferencesMock = vi.fn().mockResolvedValue(null);
vi.mock("@/lib/actions/preferences", () => ({
  updatePreferences: (...args: unknown[]) => updatePreferencesMock(...args),
}));

// `server-only` is imported transitively via the preferences action.
vi.mock("server-only", () => ({}));

// SalaryDisplayProvider context — mutable so salary hydration can be covered.
let salaryDisplayCurrency: string | null = "EUR";
let salaryDisplayPeriod: "yearly" | "monthly" | "daily" | "hourly" | null = null;
const salaryDisplayUpdateMock = vi.fn();
vi.mock("@/components/providers/SalaryDisplayProvider", () => ({
  useSalaryDisplay: () => ({
    displayCurrency: salaryDisplayCurrency,
    displayPeriod: salaryDisplayPeriod,
    rates: [],
    format: () => "",
    update: salaryDisplayUpdateMock,
  }),
}));

// localStorage is touched by `localPrefs.locale.set`; happy-dom provides
// it but with quirks. Stub a clean in-memory implementation.
let cookieValue = "";
let cookieWrites: string[] = [];

beforeEach(() => {
  pushMock.mockReset();
  refreshMock.mockReset();
  updatePreferencesMock.mockReset().mockResolvedValue(null);
  salaryDisplayCurrency = "EUR";
  salaryDisplayPeriod = null;
  salaryDisplayUpdateMock.mockReset();
  currentPathname = "/en/settings";
  cookieValue = "";
  cookieWrites = [];
  Object.defineProperty(document, "cookie", {
    configurable: true,
    get: () => cookieValue,
    set: (v: string) => {
      cookieWrites.push(v);
      // Approximate browser behaviour for the tests: the most recent
      // `NEXT_LOCALE=` write becomes the visible cookie value.
      const m = /^NEXT_LOCALE=([^;]+)/.exec(v);
      if (m) cookieValue = `NEXT_LOCALE=${m[1]}`;
    },
  });

  // localStorage stub with predictable behaviour
  const memory = new Map<string, string>();
  const stub: Storage = {
    get length() {
      return memory.size;
    },
    clear: () => memory.clear(),
    getItem: (k: string) => memory.get(k) ?? null,
    key: (i: number) => Array.from(memory.keys())[i] ?? null,
    removeItem: (k: string) => {
      memory.delete(k);
    },
    setItem: (k: string, v: string) => {
      memory.set(k, v);
    },
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: stub,
  });
});

afterEach(() => {
  vi.resetModules();
});

import { GeneralSettings } from "../GeneralSettings";

describe("GeneralSettings locale switch (#2988)", () => {
  it("exposes selected theme, locale, and job-language states", () => {
    act(() => {
      render(
        <GeneralSettings
          savedJobLanguages={[]}
          savedDisplayCurrency="EUR"
          savedSalaryPeriod={null}
          availableCurrencies={["EUR"]}
          availableLanguages={[
            { code: "en", count: 1000 },
            { code: "de", count: 500 },
          ]}
          locale="en"
        />,
      );
    });

    expect(screen.getByRole("button", { name: "Dark" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "Light" }).getAttribute("aria-pressed")).toBe("false");

    const englishButtons = screen.getAllByRole("button", { name: /English/ });
    expect(englishButtons[0]?.getAttribute("aria-pressed")).toBe("true");
    expect(englishButtons[1]?.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "All languages" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("writes NEXT_LOCALE cookie and pushes new-locale URL on Deutsch click", async () => {
    const user = userEvent.setup();
    act(() => {
      render(
        <GeneralSettings
          savedJobLanguages={[]}
          savedDisplayCurrency="EUR"
          savedSalaryPeriod={null}
          availableCurrencies={["EUR", "USD"]}
          availableLanguages={[
            { code: "en", count: 1000 },
            { code: "de", count: 500 },
          ]}
          locale="en"
        />,
      );
    });

    // The Language section uses each locale's native name as the visible
    // label — `Deutsch` for German. There is also a `Deutsch` button in
    // the Job-Languages chip row below; the Language-section button
    // appears first in DOM order so `getAllByRole(...)[0]` selects it
    // deterministically.
    const buttons = screen.getAllByRole("button", { name: /Deutsch/ });
    expect(buttons.length).toBeGreaterThan(0);
    const languageButton = buttons[0]!;

    await user.click(languageButton);

    // 1. Cookie write — the load-bearing fix for #2988. Without this,
    //    `LocaleGuard` has no signal on later same-tab navigation.
    expect(cookieWrites.some((w) => w.startsWith("NEXT_LOCALE=de"))).toBe(true);
    // The cookie write must be path=/ so it applies to every route the
    // user might land on after browser-back, not just /settings.
    expect(
      cookieWrites.find((w) => w.startsWith("NEXT_LOCALE=de"))!,
    ).toMatch(/path=\//);

    // 2. Navigation — settings page itself moves to /de/settings so the
    //    user sees confirmation. (Pre-existing behaviour; asserted to
    //    pin it down against future refactors.)
    expect(pushMock).toHaveBeenCalledWith("/de/settings");

    // 3. DB sync still happens — orthogonal to the bug but must not
    //    have been broken in passing.
    expect(updatePreferencesMock).toHaveBeenCalledWith(
      expect.objectContaining({ locale: "de" }),
    );
  });

  it("does not write the cookie when the clicked locale equals the current one", async () => {
    const user = userEvent.setup();
    act(() => {
      render(
        <GeneralSettings
          savedJobLanguages={[]}
          savedDisplayCurrency="EUR"
          savedSalaryPeriod={null}
          availableCurrencies={["EUR"]}
          availableLanguages={[]}
          locale="en"
        />,
      );
    });

    const englishBtn = screen.getAllByRole("button", { name: /English/ })[0]!;
    await user.click(englishBtn);

    expect(cookieWrites).toEqual([]);
    expect(pushMock).not.toHaveBeenCalled();
  });
});

describe("GeneralSettings overflow languages (#6027)", () => {
  it("opens Find more from all-language mode and selects an overflow language directly", async () => {
    const user = userEvent.setup();
    act(() => {
      render(
        <GeneralSettings
          savedJobLanguages={["*"]}
          savedDisplayCurrency="EUR"
          savedSalaryPeriod={null}
          availableCurrencies={["EUR"]}
          availableLanguages={[
            { code: "en", count: 1000 },
            { code: "de", count: 900 },
            { code: "fr", count: 800 },
            { code: "es", count: 700 },
            { code: "pt", count: 600 },
            { code: "ja", count: 500 },
            { code: "it", count: 400 },
            { code: "nl", count: 300 },
            { code: "pl", count: 200 },
            { code: "ko", count: 100 },
            { code: "cs", count: 90 },
            { code: "zh", count: 80 },
            { code: "sv", count: 70 },
          ]}
          locale="en"
        />,
      );
    });

    const allLanguages = screen.getByRole("button", { name: "All languages" });
    const findMore = screen.getByRole("button", { name: "Find more" });
    expect(allLanguages.getAttribute("aria-pressed")).toBe("true");
    expect((findMore as HTMLButtonElement).disabled).toBe(false);
    updatePreferencesMock.mockClear();

    await user.click(findMore);
    expect(screen.getByRole("dialog")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "svenska" }));

    expect(allLanguages.getAttribute("aria-pressed")).toBe("false");
    await waitFor(() => {
      expect(updatePreferencesMock).toHaveBeenCalledWith({
        jobLanguages: ["sv"],
      });
    });
    expect(
      updatePreferencesMock.mock.calls.filter(
        ([preferences]) =>
          typeof preferences === "object" &&
          preferences !== null &&
          "jobLanguages" in preferences &&
          Array.isArray(preferences.jobLanguages) &&
          !preferences.jobLanguages.includes("*"),
      ),
    ).toEqual([[{ jobLanguages: ["sv"] }]]);
  });
});

describe("GeneralSettings salary preference hydration (#6035)", () => {
  it("reflects the provider's rehydrated anonymous salary preferences", async () => {
    salaryDisplayCurrency = "USD";
    salaryDisplayPeriod = "monthly";

    render(
      <GeneralSettings
        savedJobLanguages={[]}
        savedDisplayCurrency="EUR"
        savedSalaryPeriod={null}
        availableCurrencies={["EUR", "USD"]}
        availableLanguages={[]}
        locale="en"
      />,
    );

    await waitFor(() => {
      expect(
        (screen.getByRole("combobox", { name: "Currency" }) as HTMLSelectElement)
          .value,
      ).toBe("USD");
      expect(
        (screen.getByRole("combobox", { name: "Pay period" }) as HTMLSelectElement)
          .value,
      ).toBe("monthly");
    });
  });
});
