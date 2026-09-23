import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  getSession: vi.fn(),
  getPreferences: vi.fn(),
  readAnonJobLanguagesCookie: vi.fn(),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSession: mocks.getSession,
}));

vi.mock("@/lib/actions/preferences", () => ({
  getPreferences: mocks.getPreferences,
}));

vi.mock("@/lib/anon-preferences", () => ({
  readAnonJobLanguagesCookie: mocks.readAnonJobLanguagesCookie,
}));

import { getViewerLanguages } from "@/lib/viewer";

describe("getViewerLanguages", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.readAnonJobLanguagesCookie.mockResolvedValue(null);
  });

  it("preserves the all-languages cookie for a signed-in viewer without a preferences row", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "user-1" } });
    mocks.getPreferences.mockResolvedValue(null);
    mocks.readAnonJobLanguagesCookie.mockResolvedValue(["*"]);

    await expect(getViewerLanguages("en")).resolves.toEqual([]);
    expect(mocks.readAnonJobLanguagesCookie).toHaveBeenCalledOnce();
  });

  it("prefers the authenticated database preference when it exists", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "user-1" } });
    mocks.getPreferences.mockResolvedValue({ jobLanguages: ["de"] });
    mocks.readAnonJobLanguagesCookie.mockResolvedValue(["*"]);

    await expect(getViewerLanguages("en")).resolves.toEqual(["de"]);
    expect(mocks.readAnonJobLanguagesCookie).not.toHaveBeenCalled();
  });
});
