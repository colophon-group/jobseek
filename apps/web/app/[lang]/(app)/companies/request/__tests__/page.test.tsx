import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// Neutralise the build-time guard transitively imported by sessionCache.
vi.mock("server-only", () => ({}));

// Mock session check — we toggle authed/unauthed per test.
const mockGetSessionUserId = vi.fn<() => Promise<string | null>>();
vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: () => mockGetSessionUserId(),
}));

// Mock next/navigation's redirect so we can observe the call without
// throwing through React's render path.
const mockRedirect = vi.fn((url: string) => {
  throw new Error(`__redirect__:${url}`);
});
vi.mock("next/navigation", () => ({
  redirect: (url: string) => mockRedirect(url),
}));

// Stub the embedded form so we can introspect the props the page passes.
vi.mock("../company-request-page-form", () => ({
  CompanyRequestPageForm: (props: {
    locale: string;
    defaultName?: string;
    defaultWebsite?: string;
  }) => (
    <div
      data-testid="form-stub"
      data-locale={props.locale}
      data-default-name={props.defaultName ?? ""}
      data-default-website={props.defaultWebsite ?? ""}
    />
  ),
}));

// i18n loader is heavy and depends on filesystem — stub it.
vi.mock("@/lib/i18n", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/i18n")>("@/lib/i18n");
  return {
    ...actual,
    loadCatalog: vi.fn(async () => ({
      i18n: {
        _: (msg: { id?: string; message?: string } | string) =>
          typeof msg === "string"
            ? msg
            : (msg.message ?? msg.id ?? ""),
      },
      messages: {},
    })),
  };
});

vi.mock("@lingui/react/server", () => ({
  setI18n: vi.fn(),
}));

import CompaniesRequestPage from "../page";

function renderPage(opts: {
  lang?: string;
  name?: string;
  website?: string;
}) {
  const params = Promise.resolve({ lang: opts.lang ?? "en" });
  const searchParams = Promise.resolve({
    name: opts.name,
    website: opts.website,
  });
  return CompaniesRequestPage({ params, searchParams });
}

beforeEach(() => {
  mockGetSessionUserId.mockReset();
  mockRedirect.mockClear();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("/[lang]/companies/request page", () => {
  it("renders with 'Anthropic' prefilled when ?name=Anthropic", async () => {
    mockGetSessionUserId.mockResolvedValue("user_1");
    const ui = await renderPage({ name: "Anthropic" });
    render(ui);

    const stub = screen.getByTestId("form-stub");
    expect(stub.getAttribute("data-default-name")).toBe("Anthropic");
    expect(stub.getAttribute("data-default-website")).toBe("");
    expect(stub.getAttribute("data-locale")).toBe("en");
  });

  it("renders with both name and website prefilled when both are present", async () => {
    mockGetSessionUserId.mockResolvedValue("user_1");
    const ui = await renderPage({
      name: "Anthropic",
      website: "https://anthropic.com",
    });
    render(ui);

    const stub = screen.getByTestId("form-stub");
    expect(stub.getAttribute("data-default-name")).toBe("Anthropic");
    expect(stub.getAttribute("data-default-website")).toBe(
      "https://anthropic.com",
    );
  });

  it("renders an empty form when no query params are provided", async () => {
    mockGetSessionUserId.mockResolvedValue("user_1");
    const ui = await renderPage({});
    render(ui);

    const stub = screen.getByTestId("form-stub");
    expect(stub.getAttribute("data-default-name")).toBe("");
    expect(stub.getAttribute("data-default-website")).toBe("");
  });

  it("renders the personalized heading when ?name is provided", async () => {
    mockGetSessionUserId.mockResolvedValue("user_1");
    const ui = await renderPage({ name: "Anthropic" });
    render(ui);

    // Heading copy: "Sorry, *Anthropic* isn't in our catalog yet. Want us to
    // add it?" — assert the company name appears in the heading.
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent ?? "").toContain("Anthropic");
  });

  it("renders the generic heading when ?name is empty", async () => {
    mockGetSessionUserId.mockResolvedValue("user_1");
    const ui = await renderPage({});
    render(ui);

    const heading = screen.getByRole("heading", { level: 1 });
    // Generic heading should not embed an empty placeholder.
    expect(heading.textContent ?? "").not.toContain("undefined");
    // And should contain a sensible prompt — we look for the word "company"
    // (case-insensitive) so the test isn't tightly-coupled to phrasing.
    expect(heading.textContent ?? "").toMatch(/company/i);
  });

  it("redirects unauthed users to the sign-in page with ?next= set", async () => {
    mockGetSessionUserId.mockResolvedValue(null);
    await expect(
      renderPage({ name: "Anthropic", lang: "en" }),
    ).rejects.toThrow(/__redirect__:/);

    expect(mockRedirect).toHaveBeenCalledTimes(1);
    const url = mockRedirect.mock.calls[0]?.[0] as string;
    expect(url).toMatch(/^\/en\/sign-in\?next=/);
    // The `next` param should encode our path + query so the user comes back.
    const decoded = decodeURIComponent(url.split("next=")[1] ?? "");
    expect(decoded).toContain("/en/companies/request");
    expect(decoded).toContain("name=Anthropic");
  });

  it("renders a body paragraph that mentions the agent flow", async () => {
    mockGetSessionUserId.mockResolvedValue("user_1");
    const ui = await renderPage({ name: "Anthropic" });
    const { container } = render(ui);

    // Body paragraph should hint at the agent / Murmur flow that fires after
    // submit. We assert "agent" appears somewhere in the page body to keep
    // the test resilient to wording changes.
    expect((container.textContent ?? "").toLowerCase()).toContain("agent");
  });
});
