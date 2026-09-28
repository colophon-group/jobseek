import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ArticleJumpLink } from "../ArticleJumpLink";

it("jumps to the clicked article's section when PPR retains a hidden duplicate", () => {
  render(
    <>
      <div hidden><main><h2 id="jobseek">Hidden instructions</h2></main></div>
      <main>
        <ArticleJumpLink href="#jobseek">Job Seek instructions</ArticleJumpLink>
        <h2 id="jobseek">Visible instructions</h2>
      </main>
    </>,
  );
  const target = screen.getByRole("heading", { name: "Visible instructions" });
  const scrollIntoView = vi.fn();
  Object.defineProperty(target, "scrollIntoView", { value: scrollIntoView });

  fireEvent.click(screen.getByRole("link", { name: "Job Seek instructions" }));

  expect(document.activeElement).toBe(target);
  expect(scrollIntoView).toHaveBeenCalledWith({ block: "start" });
  expect(window.location.hash).toBe("#jobseek");
});
