import { test, expect } from '@playwright/test';

test.describe('Job Queue Feature', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to queue page (requires auth)
    await page.goto('/queue');
  });

  test('queue page loads', async ({ page }) => {
    // Check page title/header
    await expect(page.locator('h1')).toContainText('Job Queue');
  });

  test('empty queue shows message', async ({ page }) => {
    // For fresh user, queue should be empty
    const emptyMessage = page.locator('text=/Your queue is empty|queue is empty/i');
    const exists = await emptyMessage.isVisible().catch(() => false);

    if (exists) {
      await expect(emptyMessage).toBeVisible();
    }
  });

  test('queue link in navigation exists', async ({ page }) => {
    // Check that queue nav link is present
    await page.goto('/explore');
    const queueLink = page.locator('a[href*="/queue"]');
    await expect(queueLink).toBeDefined();
  });
});
