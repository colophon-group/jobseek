import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  searchParams: new URLSearchParams(),
  verifyEmail: vi.fn(() => new Promise(() => {})),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
  useSearchParams: () => mocks.searchParams,
}));

vi.mock("@lingui/react/server", () => ({
  getI18n: () => ({
    _: ({ message, id }: { message?: string; id?: string }) => message ?? id ?? "",
  }),
}));

vi.mock("@/lib/i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/i18n")>()),
  initI18nForPage: vi.fn().mockResolvedValue("en"),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (path: string) => `/en${path}`,
}));

vi.mock("@/lib/auth-client", () => ({
  authClient: {
    requestPasswordReset: vi.fn(),
    resetPassword: vi.fn(),
    sendVerificationEmail: vi.fn(),
    verifyEmail: mocks.verifyEmail,
    signIn: {
      email: vi.fn(),
      username: vi.fn(),
      social: vi.fn(),
    },
    signUp: { email: vi.fn() },
  },
}));

vi.mock("@/lib/actions/preferences", () => ({
  getPreferences: vi.fn(),
  updatePreferences: vi.fn(),
}));

vi.mock("@/lib/preference-timestamps", () => ({
  localPrefs: {
    themeTimestamp: { get: vi.fn(), set: vi.fn() },
    localeTimestamp: { get: vi.fn(), set: vi.fn() },
    locale: { get: vi.fn(), set: vi.fn() },
  },
}));

vi.mock("@/components/HeaderShell", () => ({ HeaderShell: () => <header /> }));
vi.mock("@/components/Footer", () => ({ Footer: () => <footer /> }));
vi.mock("@/components/SkipToContentLink", () => ({
  SkipToContentLink: () => <a href="#main-content">Skip to content</a>,
}));
vi.mock("@/components/Features", () => ({ Features: () => null }));
vi.mock("@/components/Pricing", () => ({ Pricing: () => null }));
vi.mock("@/components/PublicDomainArt", () => ({ PublicDomainArt: () => null }));
vi.mock("@/components/ThemeToggleButton", () => ({ ThemeToggleButton: () => null }));
vi.mock("@/components/LocaleSwitcher", () => ({ LocaleSwitcher: () => null }));
vi.mock("@/components/ThemedImage", () => ({
  ThemedImage: ({ alt }: { alt: string }) => <span aria-label={alt} />,
}));
vi.mock("@/lib/seo", () => ({
  buildAlternates: vi.fn(),
  JsonLd: () => null,
}));

import PublicLayout from "../(public)/layout";
import HomePage from "../(public)/page";
import ForgotPasswordPage from "../(auth)/forgot-password/page";
import CheckEmailPage from "../(auth)/check-email/page";
import SignInPage from "../(auth)/sign-in/page";
import SignUpPage from "../(auth)/sign-up/page";
import ResetPasswordPage from "../reset-password/page";
import VerifyEmailPage from "../verify-email/page";
import { AuthShell } from "@/components/AuthShell";

function expectSinglePageOutline() {
  const main = screen.getByRole("main");
  const heading = screen.getByRole("heading", { level: 1 });

  expect(screen.getAllByRole("main")).toHaveLength(1);
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  expect(main.id).toBe("main-content");
  expect(main.tabIndex).toBe(-1);
  expect(main.contains(heading)).toBe(true);
  expect(heading.textContent?.trim()).not.toBe("");
  expect(screen.getAllByRole("link", { name: "Skip to content" })).toHaveLength(1);
  expect(
    screen.getByRole("link", { name: "Skip to content" }).getAttribute("href"),
  ).toBe("#main-content");
}

function renderAuthRoute(page: ReactElement, ownsShell = false) {
  render(ownsShell ? page : <AuthShell>{page}</AuthShell>);
  expectSinglePageOutline();
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  mocks.searchParams.delete("token");
  mocks.searchParams.delete("next");
});

describe("entry-route page semantics", () => {
  it("gives the homepage one focusable main landmark, one H1, and a matching skip target", async () => {
    const params = Promise.resolve({ lang: "en" });
    const page = await HomePage({ params });
    const layout = await PublicLayout({ children: page, params });

    render(layout);

    expectSinglePageOutline();
  });

  it.each([
    ["sign-in", <SignInPage />],
    ["sign-up", <SignUpPage />],
    ["forgot-password", <ForgotPasswordPage />],
  ])("gives /en/%s one main landmark and one descriptive H1", (_route, page) => {
    renderAuthRoute(page);
  });

  it("gives /en/check-email one main landmark and one descriptive H1", () => {
    sessionStorage.setItem("verify-email", "person@example.com");
    renderAuthRoute(<CheckEmailPage />);
  });

  it("gives /en/verify-email one main landmark and one descriptive H1 while loading", () => {
    mocks.searchParams.set("token", "test-token");
    renderAuthRoute(<VerifyEmailPage />, true);
  });

  it("gives /en/reset-password one main landmark and one descriptive H1", () => {
    mocks.searchParams.set("token", "test-token");
    renderAuthRoute(<ResetPasswordPage />, true);
  });
});
