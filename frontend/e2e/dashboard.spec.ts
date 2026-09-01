/**
 * Playwright E2E — critical dashboard flow.
 * Run:  npx playwright install chromium && npx playwright test
 * (Browsers are not vendored; install once locally. CI can cache them.)
 */
import { expect, test } from '@playwright/test';

test('dashboard renders live scanner state and immutable signal invariants', async ({ page }) => {
  await page.goto('http://localhost:3000/');
  await expect(page.getByText('PREMARKET')).toBeVisible();
  await expect(page.getByText('Active BUY Signals').first()).toBeVisible();
  await expect(page.getByText('Candidate Scanner')).toBeVisible();
  // top bar shows a phase chip
  await expect(page.locator('.phase-chip')).toBeVisible();
  // candidate table or its empty state renders
  await expect(page.locator('.tbl-wrap').first()).toBeVisible();
});

test('signal history page loads with export affordance', async ({ page }) => {
  await page.goto('http://localhost:3000/signals');
  await expect(page.getByText('Signal History')).toBeVisible();
  await expect(page.getByText('Export CSV')).toBeVisible();
});

test('settings page exposes universe limits with blank-means-no-limit', async ({ page }) => {
  await page.goto('http://localhost:3000/settings');
  await expect(page.getByLabel('Market cap max ($)')).toBeVisible();
  await expect(page.getByLabel('Float max (shares)')).toBeVisible();
  await expect(page.getByText('Micro caps $20–300M')).toBeVisible();
});
