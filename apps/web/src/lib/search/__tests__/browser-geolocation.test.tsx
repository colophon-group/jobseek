import { act, render, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  type BrowserCoordinates,
  useBrowserCoordinates,
} from "../browser-geolocation";

type SuccessCallback = (position: GeolocationPosition) => void;

function Harness({
  serverLat,
  onValue,
}: {
  serverLat?: number;
  onValue: (value: BrowserCoordinates | null) => void;
}) {
  const coordinates = useBrowserCoordinates(serverLat);

  useEffect(() => {
    onValue(coordinates);
  }, [coordinates, onValue]);

  return null;
}

function geolocationPosition(lat: number, lng: number): GeolocationPosition {
  return {
    coords: {
      latitude: lat,
      longitude: lng,
    },
  } as GeolocationPosition;
}

describe("useBrowserCoordinates", () => {
  const successes: SuccessCallback[] = [];
  const getCurrentPosition = vi.fn((success: SuccessCallback) => {
    successes.push(success);
  });

  beforeEach(() => {
    successes.length = 0;
    getCurrentPosition.mockClear();
    Object.defineProperty(globalThis.navigator, "geolocation", {
      configurable: true,
      value: { getCurrentPosition },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not request browser geolocation when server coordinates are already available", () => {
    const values: Array<BrowserCoordinates | null> = [];

    render(<Harness serverLat={47.37} onValue={(value) => values.push(value)} />);

    expect(getCurrentPosition).not.toHaveBeenCalled();
    expect(values).toEqual([null]);
  });

  it("shares one browser geolocation request across multiple hook consumers", async () => {
    const firstValues: Array<BrowserCoordinates | null> = [];
    const secondValues: Array<BrowserCoordinates | null> = [];

    render(
      <>
        <Harness onValue={(value) => firstValues.push(value)} />
        <Harness onValue={(value) => secondValues.push(value)} />
      </>,
    );

    expect(getCurrentPosition).toHaveBeenCalledTimes(1);
    expect(successes).toHaveLength(1);

    await act(async () => {
      successes[0](geolocationPosition(47.37, 8.54));
    });

    await waitFor(() => {
      expect(firstValues.at(-1)).toEqual({ lat: 47.37, lng: 8.54 });
      expect(secondValues.at(-1)).toEqual({ lat: 47.37, lng: 8.54 });
    });
  });
});
