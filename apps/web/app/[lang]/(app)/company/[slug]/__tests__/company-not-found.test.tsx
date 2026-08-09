import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CompanyNotFoundState } from "../company-not-found";

describe("CompanyNotFoundState", () => {
  it("offers localized recovery routes and prefills the missing slug", () => {
    render(
      <CompanyNotFoundState
        locale="de"
        slug="acme-inc"
        title="Unternehmen nicht gefunden"
        message="Das Unternehmen existiert nicht."
        exploreLabel="Entdecken"
        requestLabel="Dieses Unternehmen anfragen"
      />,
    );

    expect(screen.getByRole("link", { name: "Entdecken" }).getAttribute("href"))
      .toBe("/de/explore");
    expect(
      screen
        .getByRole("link", { name: "Dieses Unternehmen anfragen" })
        .getAttribute("href"),
    ).toBe("/de/companies/request?name=acme%20inc");
  });
});
