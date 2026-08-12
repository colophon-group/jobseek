import { StrictMode } from "react";
import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  persist: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: mocks.refresh }),
}));

vi.mock("@/lib/viewer-tz", () => ({
  persistViewerTimeZoneCookie: () => mocks.persist(),
}));

import { ViewerTimezoneCookie } from "../ViewerTimezoneCookie";

describe("ViewerTimezoneCookie (#7201)", () => {
  beforeEach(() => {
    mocks.persist.mockReset();
    mocks.refresh.mockReset();
    mocks.persist.mockReturnValue("Europe/Athens");
  });

  it("maintains the cookie without refreshing ordinary app mounts", async () => {
    render(<ViewerTimezoneCookie />);

    await waitFor(() => expect(mocks.persist).toHaveBeenCalledOnce());
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it("does not refresh stats when server and browser zones match", async () => {
    render(
      <ViewerTimezoneCookie
        serverTimeZone="Europe/Athens"
        refreshWhenChanged
      />,
    );

    await waitFor(() => expect(mocks.persist).toHaveBeenCalledOnce());
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it("refreshes the RSC payload when a direct or moved-zone visit differs", async () => {
    render(
      <StrictMode>
        <ViewerTimezoneCookie
          serverTimeZone="America/New_York"
          refreshWhenChanged
        />
      </StrictMode>,
    );

    // Strict Mode replays mount effects in development. The request guard
    // keeps that from issuing two RSC refreshes for one timezone transition.
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalledOnce());
  });
});
