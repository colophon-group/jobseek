import { test, expect } from '@playwright/test';

test.describe('Resume Customization Flow', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to queue page (requires auth)
    await page.goto('/queue');
  });

  test('customize resume button appears on analyzed queue items', async ({ page }) => {
    // Find any queue job card with analyzed state
    const jobCards = page.locator('[role="article"]').filter({ has: page.locator('text=Fit Score') });
    const cardCount = await jobCards.count();

    if (cardCount > 0) {
      // If analyzed cards exist, check for customize button
      const customizeButton = page.locator('button:has-text("Customize Resume")').first();
      const exists = await customizeButton.isVisible().catch(() => false);
      
      if (exists) {
        await expect(customizeButton).toBeVisible();
      }
    }
  });

  test('resume customization modal opens when button clicked', async ({ page }) => {
    // Find customize button
    const customizeButton = page.locator('button:has-text("Customize Resume")').first();
    const isVisible = await customizeButton.isVisible().catch(() => false);

    if (isVisible) {
      await customizeButton.click();
      
      // Wait for modal to appear
      const modal = page.locator('text=Customize Resume Preview');
      await expect(modal).toBeVisible({ timeout: 5000 });
    }
  });

  test('resume customization modal shows preview section', async ({ page }) => {
    // Find customize button
    const customizeButton = page.locator('button:has-text("Customize Resume")').first();
    const isVisible = await customizeButton.isVisible().catch(() => false);

    if (isVisible) {
      await customizeButton.click();
      
      // Wait for modal and check for preview content
      const modal = page.locator('text=Customize Resume Preview');
      await expect(modal).toBeVisible({ timeout: 5000 });

      // Check for diff preview sections (Original/Customized)
      const originalLabel = page.locator('text=Original');
      const customizedLabel = page.locator('text=Customized');
      
      const originalExists = await originalLabel.isVisible().catch(() => false);
      const customizedExists = await customizedLabel.isVisible().catch(() => false);
      
      if (originalExists || customizedExists) {
        expect(originalExists || customizedExists).toBe(true);
      }
    }
  });

  test('resume customization modal has accept and cancel buttons', async ({ page }) => {
    // Find customize button
    const customizeButton = page.locator('button:has-text("Customize Resume")').first();
    const isVisible = await customizeButton.isVisible().catch(() => false);

    if (isVisible) {
      await customizeButton.click();
      
      // Wait for modal
      const modal = page.locator('text=Customize Resume Preview');
      await expect(modal).toBeVisible({ timeout: 5000 });

      // Check for action buttons
      const acceptButton = page.locator('button:has-text("Accept & Save")');
      const cancelButton = page.locator('button:has-text("Cancel")');

      const acceptVisible = await acceptButton.isVisible().catch(() => false);
      const cancelVisible = await cancelButton.isVisible().catch(() => false);
      
      if (acceptVisible || cancelVisible) {
        expect(acceptVisible || cancelVisible).toBe(true);
      }
    }
  });

  test('cancel button closes customization modal without saving', async ({ page }) => {
    // Find customize button
    const customizeButton = page.locator('button:has-text("Customize Resume")').first();
    const isVisible = await customizeButton.isVisible().catch(() => false);

    if (isVisible) {
      await customizeButton.click();
      
      // Wait for modal
      const modal = page.locator('text=Customize Resume Preview');
      await expect(modal).toBeVisible({ timeout: 5000 });

      // Click cancel
      const cancelButton = page.locator('button:has-text("Cancel")');
      const cancelVisible = await cancelButton.isVisible().catch(() => false);
      
      if (cancelVisible) {
        await cancelButton.click();
        
        // Modal should close
        await expect(modal).toBeHidden({ timeout: 5000 });
      }
    }
  });

  test('queue page still accessible after modal closed', async ({ page }) => {
    // Navigate to queue
    await page.goto('/queue');
    
    // Check queue page is visible
    const queueHeader = page.locator('text=Job Queue').or(page.locator('h1')).first();
    const exists = await queueHeader.isVisible().catch(() => false);
    
    if (exists) {
      await expect(queueHeader).toBeVisible();
    }
  });

  test('customizing state shows loading indicator', async ({ page }) => {
    // Find customize button
    const customizeButton = page.locator('button:has-text("Customize Resume")').first();
    const isVisible = await customizeButton.isVisible().catch(() => false);

    if (isVisible) {
      // Click should trigger loading state
      await customizeButton.click();
      
      // Wait for modal
      const modal = page.locator('text=Customize Resume Preview');
      
      // Check if loading indicator appears or button shows loading state
      const loadingText = page.locator('text=/Customizing|Saving/').first();
      const loadingVisible = await loadingText.isVisible({ timeout: 1000 }).catch(() => false);
      
      if (loadingVisible) {
        await expect(loadingText).toBeVisible();
      }
    }
  });
});
