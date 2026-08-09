import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WatchlistNotFoundState } from "./watchlist-not-found";

describe("WatchlistNotFoundState", () => {
  it("offers a locale-preserving route back to the watchlist browser", () => {
    render(
      <WatchlistNotFoundState
        lang="fr"
        title="Watchlist introuvable"
        message="Cette watchlist n’existe pas."
        browseLabel="Parcourir les watchlists"
      />,
    );

    expect(
      screen
        .getByRole("link", { name: "Parcourir les watchlists" })
        .getAttribute("href"),
    ).toBe("/fr/watchlists");
  });
});
