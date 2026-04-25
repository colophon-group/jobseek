import { test, expect } from "@playwright/test";

test.describe("Search filters", () => {
  test("keyword search updates URL", async ({ page }) => {
    await page.goto("/en/explore");
    await page.waitForLoadState("load");

    const searchInput = page.locator("input").first();
    await expect(searchInput).toBeVisible({ timeout: 5_000 });

    // Type "React", wait for the suggestion dropdown, then navigate to the
    // first suggestion with ArrowDown + Enter.
    // Enter alone only works when there is exactly 1 keyword suggestion
    // (handleKeyDown:423); with real Typesense results there are multiple
    // suggestions so ArrowDown is required to highlight one first.
    await searchInput.fill("React");
    await expect(page.locator("[role='listbox']").first()).toBeVisible({ timeout: 10_000 });
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");

    await page.waitForURL(/[?&](tech=react|q=React)/i, { timeout: 60_000 });
    expect(page.url()).toMatch(/[?&](tech=react|q=React)/i);
  });

  test("clear all removes query param", async ({ page }) => {
    await page.goto("/en/explore?q=React");
    await page.waitForLoadState("load");

    // fetchExploreData makes several Typesense calls; allow extra time for
    // Turbopack first-compile on a fresh dev server (can take up to 30s).
    await expect(page.getByText("Clear all")).toBeVisible({ timeout: 60_000 });
    await page.getByText("Clear all").click();

    await page.waitForURL((url) => !url.toString().includes("q="), { timeout: 10_000 });
    expect(page.url()).not.toContain("q=");
  });
});
