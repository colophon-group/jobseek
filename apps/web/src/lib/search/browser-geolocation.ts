import { useEffect, useState } from "react";

export type BrowserCoordinates = {
  lat: number;
  lng: number;
};

let browserCoordinatesRequest: Promise<BrowserCoordinates | null> | null = null;

export function getBrowserCoordinatesOnce(): Promise<BrowserCoordinates | null> {
  if (typeof navigator === "undefined" || !navigator.geolocation) {
    return Promise.resolve(null);
  }

  browserCoordinatesRequest ??= new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude });
      },
      () => resolve(null),
      { maximumAge: 600_000, timeout: 5_000 },
    );
  });

  return browserCoordinatesRequest;
}

export function useBrowserCoordinates(
  serverLat: number | null | undefined,
): BrowserCoordinates | null {
  const [coordinates, setCoordinates] = useState<BrowserCoordinates | null>(null);

  useEffect(() => {
    if (serverLat != null) {
      setCoordinates(null);
      return;
    }

    let cancelled = false;

    void getBrowserCoordinatesOnce().then((geo) => {
      if (!cancelled) {
        setCoordinates(geo);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [serverLat]);

  return coordinates;
}
