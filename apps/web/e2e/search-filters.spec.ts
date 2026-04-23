import { test, expect } from "@playwright/test";

test.describe("Search filters", () => {
  test("keyword search updates URL", async ({ page }) => {
    await page.goto("/en/explore");
    await page.waitForLoadState("load");

    const searchInput = page.locator("input").first();
    await expect(searchInput).toBeVisible({ timeout: 5_000 });

    // Type the keyword — header search bar builds dropdown from typed text
    await searchInput.fill("React");

    // Wait for the keyword suggestion to appear in the dropdown and click it
    const keywordOption = page.locator('[role="option"][data-suggestion]').first();
    await expect(keywordOption).toBeVisible({ timeout: 5_000 });
    await keywordOption.click();

    // router.push navigates; wait for URL to update (allow time for App Router navigation)
    await page.waitForURL(/[?&]q=React/, { timeout: 20_000 });
    expect(page.url()).toMatch(/[?&]q=React/);
  });

  test("clear all removes query param", async ({ page }) => {
    await page.goto("/en/explore?q=React");
    await page.waitForLoadState("load");

    // The SearchPage (which has the toolbar with "Clear all") renders after
    // fetchExploreData resolves. With Typesense down this takes ~5-15s.
    await expect(page.getByText("Clear all")).toBeVisible({ timeout: 20_000 });
    await page.getByText("Clear all").click();

    await page.waitForURL((url) => !url.toString().includes("q="), { timeout: 10_000 });
    expect(page.url()).not.toContain("q=");
  });
});
