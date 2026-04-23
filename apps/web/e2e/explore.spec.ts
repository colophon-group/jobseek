import { test, expect } from "@playwright/test";

test.describe("Explore page", () => {
  test("loads and shows job postings", async ({ page }) => {
    await page.goto("/en/explore");
    await page.waitForLoadState("load");

    // At least one posting row (div[role=button]) should be visible.
    // Requires Typesense to be running with job data.
    const firstPosting = page.locator("div[role='button']").first();
    await expect(firstPosting).toBeVisible({ timeout: 20_000 });
  });

  test("search bar is present", async ({ page }) => {
    await page.goto("/en/explore");
    const searchInput = page.locator("input").first();
    await expect(searchInput).toBeVisible({ timeout: 5_000 });
  });
});
