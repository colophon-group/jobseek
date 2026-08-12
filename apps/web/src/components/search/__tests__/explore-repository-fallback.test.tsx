import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import "@/test-utils/lingui-mock";
import { ExploreRepositoryFallback } from "../explore-repository-fallback";

const companies = [
  {
    name: "Acme",
    slug: "acme",
  },
  {
    name: "Example Labs",
    slug: "example-labs",
  },
];

describe("ExploreRepositoryFallback", () => {
  it("labels the degraded state and offers profile links without job or save controls", () => {
    const { container } = render(
      <ExploreRepositoryFallback locale="de" companies={companies} />,
    );

    expect(screen.getByRole("alert").textContent).toMatch(
      /live job results are temporarily unavailable/i,
    );
    expect(screen.getByRole("alert").textContent).toMatch(/try refreshing the page/i);
    expect(screen.getByRole("link", { name: /Acme/ }).getAttribute("href")).toBe(
      "/de/company/acme",
    );
    expect(screen.getByRole("link", { name: "Example Labs" }).getAttribute("href")).toBe(
      "/de/company/example-labs",
    );
    expect(container.querySelectorAll("[data-search-result-company]")).toHaveLength(2);
    expect(container.querySelector("[data-posting-id]")).toBeNull();
    expect(container.querySelector("button")).toBeNull();
    expect(container.textContent).not.toMatch(/active jobs?/i);
  });

  it("uses unique accessible heading IDs for the static and hydrated copies", () => {
    const { container } = render(
      <>
        <ExploreRepositoryFallback locale="en" companies={companies} />
        <ExploreRepositoryFallback locale="en" companies={companies} />
      </>,
    );

    const sections = container.querySelectorAll("[data-explore-repository-fallback]");
    const labelledBy = Array.from(sections, (section) => section.getAttribute("aria-labelledby"));
    expect(new Set(labelledBy).size).toBe(2);
    for (const id of labelledBy) {
      expect(id).toBeTruthy();
      expect(container.querySelector(`[id="${id}"]`)).not.toBeNull();
    }
  });
});
