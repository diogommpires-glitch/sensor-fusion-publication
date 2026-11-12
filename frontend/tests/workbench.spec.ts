import { expect, type Page, test } from '@playwright/test';

async function settledPlots(page: Page) {
  await expect
    .poll(() =>
      page.locator('.js-plotly-plot').evaluateAll((plots) =>
        plots.every((plot) => {
          const layout = (plot as unknown as { _fullLayout?: { width: number } })._fullLayout;
          const container = plot.closest('.plot-wrap');
          const svg = plot.querySelector('.svg-container');
          if (!layout || !container || !svg) return false;
          const width = container.getBoundingClientRect().width;
          return (
            width > 0 &&
            Math.abs(layout.width - width) < 2 &&
            Math.abs(svg.getBoundingClientRect().width - width) < 2
          );
        }),
      ),
    )
    .toBe(true);
}

async function executeFixture(page: Page, method: 'M2' | 'M7') {
  await page.getByRole('link', { name: 'Experiment builder', exact: true }).click();
  await page.locator(`input[name="method"][value="${method}"]`).check();
  await page.getByText('Physical settings and reproducibility', { exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Fixture duration (s)', exact: true }).fill('5');
  const submitted = page.waitForResponse(
    (response) => response.url().endsWith('/api/runs') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Run experiment', exact: true }).click();
  const response = await submitted;
  expect(response.status()).toBe(202);
  const { run_id } = await response.json();
  await expect(page.locator('.run-record .status')).toHaveText('completed', { timeout: 90_000 });
  await expect(
    page.getByRole('img', { name: '3D trajectory and anchors', exact: true }),
  ).toBeVisible();
  return run_id as string;
}

test('actual local execution, replay, provenance, exports and paired comparison', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  await expect(
    page.getByText('Synthetic validation fixture, not a real-data study.', { exact: false }),
  ).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('experiment-1280.png'), fullPage: true });
  const first = await executeFixture(page, 'M7');
  await expect(page.locator('.metric-strip')).not.toContainText('Unavailable');
  await page.getByRole('slider', { name: 'Shared time cursor' }).fill('2.5');
  await expect(page).toHaveURL(/time=2.5/);
  await page.getByRole('button', { name: 'XZ', exact: true }).click();
  await expect(
    page.getByRole('img', { name: 'XZ trajectory and anchors', exact: true }),
  ).toBeVisible();
  await page.getByLabel('Update outcome').selectOption('rejected');
  await expect(page).toHaveURL(/outcome=rejected/);
  await page.getByText('Saved configuration, provenance and exports', { exact: true }).click();
  await expect(page.getByRole('link', { name: 'numerical.h5', exact: true })).toBeVisible();
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('link', { name: 'trajectory.csv', exact: true }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe('trajectory.csv');
  await page.getByText('Saved configuration, provenance and exports', { exact: true }).click();
  await page.getByLabel('Update outcome').selectOption('all');
  await page.locator('.js-plotly-plot').first().waitFor();
  await settledPlots(page);
  await page.screenshot({ path: test.info().outputPath('replay-1280.png'), fullPage: true });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await settledPlots(page);
  const wideScreenshot = await page.screenshot({
    path: test.info().outputPath('replay-1920.png'),
    fullPage: true,
  });
  expect(wideScreenshot.readUInt32BE(16)).toBe(1920);
  await page.setViewportSize({ width: 390, height: 844 });
  await settledPlots(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  const narrowScreenshot = await page.screenshot({
    path: test.info().outputPath('replay-390.png'),
    fullPage: true,
  });
  expect(narrowScreenshot.readUInt32BE(16)).toBe(390);
  await page.setViewportSize({ width: 1280, height: 900 });
  await settledPlots(page);
  const second = await executeFixture(page, 'M2');
  await page.getByRole('button', { name: 'Compare this run' }).click();
  await page.getByLabel('Run A', { exact: true }).selectOption(first);
  await page.getByLabel('Run B', { exact: true }).selectOption(second);
  await expect(
    page.getByRole('img', { name: 'Position errors for the two selected runs' }),
  ).toBeVisible();
  await expect(page.locator('.comparison-table')).toContainText('3D position RMSE');
  await expect(page.locator('.comparison-table')).not.toContainText('Unavailable');
  await page.screenshot({ path: test.info().outputPath('comparison-1280.png'), fullPage: true });
  expect(errors).toEqual([]);
});

test('blocked registered datasets retain their specific reason and cannot run', async ({
  page,
}) => {
  await page.goto('/?tab=experiment');
  await page.getByLabel('Dataset', { exact: true }).selectOption('miluv');
  await expect(page.getByRole('button', { name: 'Run experiment', exact: true })).toBeDisabled();
  await expect(page.getByText('Dataset unavailable.', { exact: false })).toContainText('missing');
  await page.getByLabel('Dataset', { exact: true }).selectOption('roboloc-g');
  await expect(page.locator('select[name="session_id"] option[value="circle"]')).toHaveText(
    'circle (final)',
  );
  await page.locator('select[name="session_id"]').selectOption('circle');
  await expect(page.getByText('Protected final session.', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run experiment', exact: true })).toBeDisabled();
  await page.getByRole('link', { name: 'Datasets & calibration', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Datasets and calibration' })).toBeVisible();
  await expect(page.locator('table').first()).toContainText('blocked');
});

test('cancellation preserves the actual run status and does not show numerical success', async ({
  page,
}) => {
  await page.goto('/?tab=experiment');
  await page.getByText('Physical settings and reproducibility', { exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Fixture duration (s)', exact: true }).fill('300');
  await page.getByRole('button', { name: 'Run experiment', exact: true }).click();
  await page.getByRole('button', { name: 'Cancel run', exact: true }).click();
  await expect(page.locator('.run-record .status')).toHaveText('cancelled', { timeout: 90_000 });
  await expect(page.locator('.metric-strip')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Run cancelled', exact: true })).toBeVisible();
});

test('API failures are visible and retry restores the region', async ({ page }) => {
  await page.route('**/api/catalog', (route) =>
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Test-only local engine unavailable' }),
    }),
  );
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('Test-only local engine unavailable');
  await page.unroute('**/api/catalog');
  await page.getByRole('button', { name: 'Refresh records', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Build an experiment', exact: true }),
  ).toBeVisible();
});

test('study screen exposes actual blockers and never substitutes fixture results', async ({
  page,
}) => {
  await page.goto('/?tab=study');
  await expect(
    page.getByRole('heading', { name: 'Study gate is incomplete', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'No completed frozen study', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Eight-method comparison', exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('study-1280.png'), fullPage: true });
});

test('viewer preserves missing reference and WLS samples and uses saved evaluation errors', async ({
  page,
}) => {
  await page.goto('/');
  const runId = await executeFixture(page, 'M7');
  await page.route(`**/api/runs/${runId}/trajectory?*`, async (route) => {
    const response = await route.fetch();
    const trajectory = await response.json();
    // Test-only boundary payload: JSON null components must never become origin coordinates.
    trajectory.positions[0] = [null, null, null];
    trajectory.reference_positions[0] = [3, 4, 0];
    trajectory.positions[1] = [3, 4, 0];
    trajectory.reference_positions[1] = [null, null, null];
    trajectory.positions[2] = [1, null, 2];
    trajectory.reference_positions[2] = [1, 2, null];
    trajectory.positions[3] = [10, 20, 30];
    trajectory.covariance_diagonal[0] = [null, null, null];
    trajectory.biases[0] = [null, null, null];
    trajectory.biases[1] = [0.1, null, 0.3];
    trajectory.position_errors.splice(0, 4, null, null, null, 1.2345);
    trajectory.confidence_probability = 0.95;
    trajectory.confidence_ellipsoid_axes = Array.from(
      { length: trajectory.times.length },
      () => null,
    );
    trajectory.confidence_ellipse_xy_axes = Array.from(
      { length: trajectory.times.length },
      () => null,
    );
    trajectory.confidence_ellipse_xz_axes = Array.from(
      { length: trajectory.times.length },
      () => null,
    );
    trajectory.confidence_ellipsoid_axes[3] = [
      [0, -0.2, 0],
      [0.1, 0, 0],
      [0, 0, 0.3],
    ];
    trajectory.confidence_ellipse_xy_axes[3] = [
      [0, -0.4],
      [0.5, 0],
    ];
    trajectory.confidence_ellipse_xz_axes[3] = [
      [0.6, 0],
      [0, 0.7],
    ];
    await route.fulfill({ response, json: trajectory });
  });
  await page.reload();
  const errorPlot = page
    .getByRole('img', { name: 'Position error against evaluation reference over time' })
    .locator('.js-plotly-plot');
  await expect
    .poll(() =>
      errorPlot.evaluate((element) =>
        (element as unknown as { data: { y: unknown[] }[] }).data?.[0]?.y.slice(0, 4),
      ),
    )
    .toEqual([null, null, null, 1.2345]);
  const spatial = page
    .getByRole('img', { name: '3D trajectory and anchors', exact: true })
    .locator('.js-plotly-plot');
  const traces = await spatial.evaluate(
    (element) =>
      (element as unknown as { data: { name: string; x: unknown[]; y: unknown[]; z: unknown[] }[] })
        .data,
  );
  const estimate = traces.find((trace) => trace.name === 'Estimate');
  const reference = traces.find((trace) => trace.name === 'Evaluation reference');
  expect(estimate?.x.slice(0, 3)).toEqual([null, 3, null]);
  expect(estimate?.y.slice(0, 3)).toEqual([null, 4, null]);
  expect(estimate?.z.slice(0, 3)).toEqual([null, 0, null]);
  expect(reference?.x[1]).toBeNull();
  expect(reference?.x[2]).toBeNull();
  expect(traces.find((trace) => trace.name === 'Time cursor')).toBeUndefined();
  expect(traces.find((trace) => trace.name.includes('ellipsoid'))).toBeUndefined();
  await expect(page.locator('.cursor-state table tbody td')).toHaveText(
    Array(6).fill('Unavailable'),
  );
  const biasPlot = page
    .getByRole('img', { name: 'Estimated residual IMU bias components' })
    .locator('.js-plotly-plot');
  await expect
    .poll(() =>
      biasPlot.evaluate((element) =>
        (element as unknown as { data: { y: unknown[] }[] }).data?.map((trace) =>
          trace.y.slice(0, 2),
        ),
      ),
    )
    .toEqual([
      [null, null],
      [null, null],
      [null, null],
    ]);
  await page.getByRole('slider', { name: 'Shared time cursor' }).fill('0.15');
  await expect
    .poll(() =>
      spatial.evaluate((element) => {
        const trace = (
          element as unknown as { data: { name: string; x: number[]; y: number[]; z: number[] }[] }
        ).data?.find((item) => item.name === 'Nominal 95% position ellipsoid');
        return trace ? [trace.x[0], trace.y[0], trace.z[0]] : null;
      }),
    )
    .toEqual([10, 20.1, 30]);
  const xyPlot = page
    .getByRole('img', { name: 'XY trajectory and anchors', exact: true })
    .locator('.js-plotly-plot');
  await expect
    .poll(() =>
      xyPlot.evaluate((element) => {
        const trace = (
          element as unknown as { data: { name: string; x: number[]; y: number[] }[] }
        ).data?.find((item) => item.name === 'Nominal 95% marginal ellipse');
        return trace ? [trace.x[0], trace.y[0]] : null;
      }),
    )
    .toEqual([10, 20.5]);
  await page.getByRole('button', { name: 'XZ', exact: true }).click();
  const xzPlot = page
    .getByRole('img', { name: 'XZ trajectory and anchors', exact: true })
    .locator('.js-plotly-plot');
  await expect
    .poll(() =>
      xzPlot.evaluate((element) => {
        const trace = (
          element as unknown as { data: { name: string; x: number[]; y: number[] }[] }
        ).data?.find((item) => item.name === 'Nominal 95% marginal ellipse');
        return trace ? [trace.x[0], trace.y[0]] : null;
      }),
    )
    .toEqual([10.6, 30]);
});

test('comparison detects nested calibration, scientific fingerprint and physical configuration differences', async ({
  page,
}) => {
  await page.goto('/');
  const first = await executeFixture(page, 'M7');
  const second = await executeFixture(page, 'M2');
  let fingerprint: string | undefined = 'test-only-different-source-fingerprint';
  await page.route(`**/api/runs/${second}`, async (route) => {
    const response = await route.fetch();
    const run = await response.json();
    run.provenance.session.calibration_hash = 'test-only-different-nested-calibration';
    run.provenance.scientific_source_hash = fingerprint;
    run.provenance.raw_sources = [
      { name: 'test-only-source.zip', sha256: 'test-only-different-archive-hash' },
    ];
    run.config.q_cv = 0.3;
    await route.fulfill({ response, json: run });
  });
  await page.getByRole('button', { name: 'Compare this run' }).click();
  await page.getByLabel('Run A', { exact: true }).selectOption(first);
  await page.getByLabel('Run B', { exact: true }).selectOption(second);
  await expect(page.locator('.comparison .notice')).toContainText('Calibration differs.');
  await expect(page.locator('.comparison .notice')).toContainText(
    'Scientific source fingerprints differ.',
  );
  await expect(page.locator('.comparison .notice')).toContainText('q_cv');
  await expect(page.locator('.comparison .notice')).toContainText(
    'Raw source archive hashes differ.',
  );
  fingerprint = undefined;
  await page.getByRole('button', { name: 'Refresh records', exact: true }).click();
  await expect(page.locator('.comparison .notice')).toContainText(
    'A scientific source fingerprint is unavailable.',
  );
});

test('recorded radar substitutions adopt the selected calibration clustering parameters', async ({
  page,
}) => {
  await page.route('**/api/catalog', async (route) => {
    const response = await route.fetch();
    const catalog = await response.json();
    const recorded = catalog.datasets.find((dataset: { id: string }) => dataset.id === 'roboloc-g');
    recorded.status = 'partial';
    recorded.reason = 'Test-only catalogue for frozen clustering controls.';
    for (const session of recorded.sessions)
      if (session.partition === 'development') session.status = 'ready';
    catalog.calibrations.push({
      id: 'test-calibration',
      dataset_id: 'roboloc-g',
      split: 'development',
      certified_modalities: ['uwb', 'ftm', 'imu', 'radar'],
      radars: {
        AOP: { certified: true, selected_clustering: { radius_m: 0.2, min_samples: 3 } },
        ISK0: { certified: true, selected_clustering: { radius_m: 0.4, min_samples: 5 } },
        ISK1: { certified: true, selected_clustering: { radius_m: 0.4, min_samples: 10 } },
      },
    });
    await route.fulfill({ response, json: catalog });
  });
  await page.goto('/');
  await page.getByLabel('Dataset', { exact: true }).selectOption('roboloc-g');
  await page.locator('select[name="calibration_id"]').selectOption('test-calibration');
  await page.getByText('Physical settings and reproducibility', { exact: true }).click();
  const radius = page.getByRole('spinbutton', { name: 'Radar clustering radius (m)', exact: true });
  const minimum = page.getByRole('spinbutton', {
    name: 'Radar cluster minimum detections',
    exact: true,
  });
  await expect(radius).toHaveValue('0.2');
  await expect(minimum).toHaveValue('3');
  await expect(radius).toBeDisabled();
  await page.locator('select[name="radar_sensor"]').selectOption('ISK0');
  await expect(radius).toHaveValue('0.4');
  await expect(minimum).toHaveValue('5');
  await page.locator('select[name="radar_sensor"]').selectOption('ISK1');
  await expect(radius).toHaveValue('0.4');
  await expect(minimum).toHaveValue('10');
});
