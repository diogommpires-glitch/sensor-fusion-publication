import { expect, test } from '@playwright/test';
import type { Catalog } from '../src/types';

test('a mobile deep link keeps the active workbench tab visible after resizing', async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/?tab=research');
  const navigation = page.getByRole('navigation', { name: 'Workbench views' });
  const active = navigation.getByRole('link', { name: 'Research & hardware', exact: true });
  await expect(active).toHaveAttribute('aria-current', 'page');
  for (const width of [375, 1920, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await expect
      .poll(() =>
        active.evaluate((link) => {
          const tab = link.getBoundingClientRect();
          const nav = link.closest('nav')?.getBoundingClientRect();
          return !!nav && tab.left >= nav.left - 1 && tab.right <= nav.right + 1;
        }),
      )
      .toBe(true);
  }
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test('calibration identities and mobile session columns remain inspectable', async ({
  page,
  request,
}) => {
  const catalog: Catalog = await (await request.get('/api/catalog')).json();
  catalog.calibrations = [
    { calibration_id: 'development-test-only', dataset_id: 'miluv' },
    { id: 'named-test-only', name: 'Named test calibration' },
    { dataset: 'test-only-invalid-shape' },
  ];
  const miluv = catalog.datasets.find((dataset) => dataset.id === 'miluv');
  if (!miluv) throw new Error('The test catalog must register MILUV.');
  miluv.sessions = [
    {
      id: 'default_1_random3_0',
      label: 'default_1_random3_0',
      partition: 'development',
      status: 'ready',
    },
    {
      id: 'default_1_random3_2',
      label: 'default_1_random3_2',
      partition: 'test',
      status: 'protected',
    },
  ];
  // These catalog records exercise presentation only; they are not sensor calibrations.
  await page.route('**/api/catalog', (route) => route.fulfill({ json: catalog }));
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/?tab=datasets');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Datasets and calibration');
  await expect(
    page.getByRole('heading', { level: 2, name: 'Datasets and calibration', exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText(
      'Access, frame certification and development-only calibration precede scientific claims.',
    ),
  ).toBeVisible();
  await expect(
    page.locator('summary').filter({ hasText: /^development-test-only$/ }),
  ).toBeVisible();
  await expect(
    page.locator('summary').filter({ hasText: /^Named test calibration$/ }),
  ).toBeVisible();
  await expect(page.locator('.dataset-view summary')).not.toContainText(['undefined']);
  await page
    .locator('summary')
    .filter({ hasText: /^Unidentified calibration record$/ })
    .click();
  await expect(
    page.getByRole('status').filter({ hasText: 'This record has no calibration identifier.' }),
  ).toBeVisible();
  await expect(page.getByText('test-only-invalid-shape', { exact: true })).toBeVisible();

  const summary = page.locator('summary').filter({ hasText: /^MILUV$/ });
  await summary.click();
  const sessions = page.getByRole('region', { name: 'MILUV sessions', exact: true });
  await expect(sessions).toBeVisible();
  await summary.focus();
  await page.keyboard.press('Tab');
  await expect(sessions).toBeFocused();
  expect(
    await sessions.evaluate((region) => region.getBoundingClientRect().right <= innerWidth),
  ).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => sessions.evaluate((region) => region.scrollLeft)).toBeGreaterThan(0);
  await expect(sessions).toContainText('protected');
});

test('the actual Plotly toolbar has visible keyboard focus on the dark plot', async ({
  page,
  request,
}, testInfo) => {
  const catalog: Catalog = await (await request.get('/api/catalog')).json();
  const response = await request.post('/api/runs', {
    data: { ...catalog.default_config, method: 'M2', duration_s: 5 },
  });
  expect(response.status(), await response.text()).toBe(202);
  const { run_id } = await response.json();
  await expect
    .poll(async () => (await (await request.get(`/api/runs/${run_id}`)).json()).status, {
      timeout: 30_000,
    })
    .toBe('completed');
  await page.goto(`/?tab=replay&run=${run_id}`);
  const toolbarButton = page.locator('.modebar-btn').first();
  await expect(toolbarButton).toBeAttached();
  await expect
    .poll(() =>
      page
        .locator('.js-plotly-plot')
        .first()
        .evaluate((plot) => {
          const layout = (plot as unknown as { _fullLayout?: { width: number } })._fullLayout;
          const width = plot.closest('.plot-wrap')?.getBoundingClientRect().width;
          return !!layout && width !== undefined && Math.abs(layout.width - width) < 2;
        }),
    )
    .toBe(true);
  await page.keyboard.press('Tab');
  await toolbarButton.focus();
  const focus = await toolbarButton.evaluate((button) => {
    const style = getComputedStyle(button);
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('A canvas context is required for the colour contrast check.');
    const luminance = (colour: string) => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = colour;
      context.fillRect(0, 0, 1, 1);
      const rgb = [...context.getImageData(0, 0, 1, 1).data].slice(0, 3).map((channel) => {
        const value = channel / 255;
        return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
    };
    const ink = luminance(style.outlineColor);
    const paper = luminance(
      getComputedStyle(document.documentElement).getPropertyValue('--plot-paper'),
    );
    return {
      visible: button.matches(':focus-visible'),
      width: Number.parseFloat(style.outlineWidth),
      contrast: (Math.max(ink, paper) + 0.05) / (Math.min(ink, paper) + 0.05),
    };
  });
  expect(focus.visible).toBe(true);
  expect(focus.width).toBeGreaterThanOrEqual(2);
  expect(focus.contrast).toBeGreaterThanOrEqual(3);
  await page.screenshot({ path: testInfo.outputPath('plot-toolbar-keyboard-focus.png') });
});
