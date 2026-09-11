import { describe, expect, it, vi } from "vitest";

const { notFoundMock, publicWatchlistLookupMock, imageResponseMock } = vi.hoisted(
  () => ({
    notFoundMock: vi.fn(() => {
      throw new Error("NEXT_HTTP_ERROR_FALLBACK;404");
    }),
    publicWatchlistLookupMock: vi.fn(),
    imageResponseMock: vi.fn(),
  }),
);

vi.mock("next/navigation", () => ({ notFound: notFoundMock }));
vi.mock("next/og", () => ({ ImageResponse: imageResponseMock }));
vi.mock("@/lib/actions/watchlists", () => ({
  getPublicWatchlistByUserAndSlug: publicWatchlistLookupMock,
}));

import { GET } from "./route";

describe("legacy public watchlist OG route", () => {
  it("always returns not-found without fetching or rendering metadata", () => {
    expect(() => GET()).toThrow("NEXT_HTTP_ERROR_FALLBACK;404");
    expect(notFoundMock).toHaveBeenCalledOnce();
    expect(publicWatchlistLookupMock).not.toHaveBeenCalled();
    expect(imageResponseMock).not.toHaveBeenCalled();
  });
});
