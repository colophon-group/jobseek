import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
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

// SalaryDisplayProvider context — minimal stub so the component renders.
vi.mock("@/components/providers/SalaryDisplayProvider", () => ({
  useSalaryDisplay: () => ({
    displayCurrency: "EUR",
    displayPeriod: null,
    rates: [],
    format: () => "",
    update: vi.fn(),
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
