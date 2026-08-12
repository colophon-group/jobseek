import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import "@/test-utils/lingui-mock";
import Loading from "../loading";

describe("Explore initial document fallback", () => {
  it("keeps one page heading while cached results resume", () => {
    render(<Loading />);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "Explore Jobs",
    );
    expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
  });
});
