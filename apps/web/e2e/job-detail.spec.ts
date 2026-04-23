import { test, expect } from "@playwright/test";

test.describe("Job detail panel", () => {
  test("opens when clicking a posting", async ({ page }) => {
    await page.goto("/en/explore");
    await page.waitForLoadState("load");

    // Requires Typesense to be running with job data
    const firstPosting = page.locator("div[role='button']").first();
    await expect(firstPosting).toBeVisible({ timeout: 20_000 });
    await firstPosting.click();

    await expect(page.getByText("Job Details")).toBeVisible({ timeout: 5_000 });
  });

  test("shows View posting link after detail loads", async ({ page }) => {
    await page.goto("/en/explore");
    await page.waitForLoadState("load");

    const firstPosting = page.locator("div[role='button']").first();
    await expect(firstPosting).toBeVisible({ timeout: 20_000 });
    await firstPosting.click();
    await expect(page.getByText("Job Details")).toBeVisible({ timeout: 5_000 });

    // Detail content loads async — wait for the external link to appear
    await expect(page.getByText("View posting")).toBeVisible({ timeout: 10_000 });
  });

  // Requires R2_DOMAIN_URL to be set so job descriptions load
  test("AI summary renders after description loads", async ({ page }) => {
    test.skip(!process.env.R2_DOMAIN_URL, "R2_DOMAIN_URL not configured — skipping AI summary test");

    await page.goto("/en/explore");
    await page.waitForLoadState("load");

    const firstPosting = page.locator("div[role='button']").first();
    await expect(firstPosting).toBeVisible({ timeout: 20_000 });
    await firstPosting.click();
    await expect(page.getByText("View posting")).toBeVisible({ timeout: 10_000 });

    // AI summary block has a left border — wait for either loading or loaded state
    const summaryBlock = page.locator(".border-l-2").first();
    await expect(summaryBlock).toBeVisible({ timeout: 15_000 });
  });
});
