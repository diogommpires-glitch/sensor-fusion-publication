import { expect, test } from '@playwright/test';

test('actual research catalog preserves v1 identity and explicitly blocks unavailable physical hardware', async ({
  page,
  request,
}) => {
  const response = await request.get('/api/research');
  expect(response.ok()).toBe(true);
  const catalog = await response.json();
  expect(catalog.historical_v1.scientific_source_hash).toBe(
    'b1ee916e916667b6208cea424cbbd351a432cf700cc86afb14fe041aaed168e2',
  );
  expect(catalog.hardware.schedule).toHaveLength(87);
  expect(catalog.hardware.trials).toHaveLength(0);
  await page.goto('/?tab=research');
  const skipLink = page.locator('.skip-link');
  await expect(skipLink).not.toBeFocused();
  await expect
    .poll(() => skipLink.evaluate((link) => link.getBoundingClientRect().bottom))
    .toBeLessThanOrEqual(0);
  await page.keyboard.press('Tab');
  await expect(skipLink).toBeFocused();
  await expect
    .poll(() => skipLink.evaluate((link) => link.getBoundingClientRect().top))
    .toBeGreaterThanOrEqual(0);
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#workbench$/);
  await page.getByRole('heading', { name: 'Research & hardware', exact: true }).click();
  await expect(skipLink).not.toBeFocused();
  await expect(page.getByRole('heading', { name: 'Historical v1 is preserved' })).toBeVisible();
  await expect(page.getByText('Planned conditions: 87.', { exact: false })).toContainText(
    'Recorded trials: 0',
  );
  expect(catalog.hardware.physical_inventory_verified).toBe(false);
  expect(catalog.hardware.blocking_reasons.length).toBeGreaterThan(0);
  for (const reason of catalog.hardware.blocking_reasons) {
    await expect(page.getByText(reason, { exact: true })).toBeVisible();
  }
  await expect(page.getByRole('heading', { name: 'No range recording selected' })).toBeVisible();
  await page.getByText('Historical wheel and source fingerprints', { exact: true }).click();
  await expect(page.getByText(catalog.historical_v1.wheel_sha256, { exact: true })).toBeVisible();
  await page.getByText('Registered physical trial schedule', { exact: true }).click();
  await expect(page.locator('.research-view table tbody tr')).toHaveCount(87);
  await page.getByText('Registered physical trial schedule', { exact: true }).click();
  await page.getByText('Historical wheel and source fingerprints', { exact: true }).click();
  await page.getByRole('heading', { name: 'Research & hardware', exact: true }).click();
  // A scrolled full-page Chromium capture can include an offscreen fixed skip link.
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await expect
    .poll(() => skipLink.evaluate((link) => link.getBoundingClientRect().bottom))
    .toBeLessThanOrEqual(0);
  await page.screenshot({ path: test.info().outputPath('research-readiness.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true);
  await page.screenshot({
    path: test.info().outputPath('research-readiness-mobile.png'),
    fullPage: true,
  });
});

test('synthetic hardware replay keeps received packets separate from verified fresh ranges', async ({
  page,
  request,
}) => {
  const catalog = await (await request.get('/api/research')).json();
  catalog.hardware.trials = [
    {
      record_id: 'test-only-synthetic-range',
      status: 'completed',
      received_packets: 4,
      fresh_observations: 2,
    },
  ];
  await page.route('**/api/research', (route) => route.fulfill({ json: catalog }));
  const fixture = {
    metadata: {
      record_id: 'test-only-synthetic-range',
      kind: 'processed_radar_range',
      dimension: 1,
      synthetic: true,
      reference_ready: false,
      reference: { reference_uncertainty_m: 0.02, reference_instrument: null },
      capability: { model: 'MR60BHA2', freshness: 'sequence', source_kind: 'synthetic_fixture' },
      scope: 'Test-only synthetic torso-plane fixture.',
    },
    observations: [
      {
        time_s: 0,
        distance_m: 1,
        age_dist_ms: 0,
        new_observation: true,
        clock_basis: 'host_monotonic_receive',
        reason: 'fresh',
      },
      {
        time_s: 1,
        distance_m: 1,
        age_dist_ms: 1000,
        new_observation: false,
        clock_basis: 'host_monotonic_receive',
        reason: 'held_or_reset_sequence',
      },
      {
        time_s: 2,
        distance_m: null,
        age_dist_ms: null,
        new_observation: false,
        clock_basis: 'host_monotonic_receive',
        reason: 'invalid_packet',
      },
      {
        time_s: 3,
        distance_m: 0.9,
        age_dist_ms: 0,
        new_observation: true,
        clock_basis: 'host_monotonic_receive',
        reason: 'fresh',
      },
    ],
    dimension: 1,
    length_unit: 'm',
    scope: 'Synthetic fixture only; no physical measurement.',
  };
  await page.route('**/api/hardware/trials/test-only-synthetic-range', (route) =>
    route.fulfill({ json: fixture }),
  );
  await page.goto('/?tab=research&trial=test-only-synthetic-range');
  await expect(
    page.getByText('Synthetic hardware fixture. No physical measurement or accuracy claim.'),
  ).toBeVisible();
  await expect(page.getByText('Reference evidence is incomplete.', { exact: false })).toBeVisible();
  await expect(
    page.getByRole('img', { name: 'One-dimensional processed radar range and verified freshness' }),
  ).toBeVisible();
  await expect
    .poll(() =>
      page
        .locator('.js-plotly-plot')
        .first()
        .evaluate((element) =>
          (element as unknown as { data?: { y: unknown[] }[] }).data?.map((trace) => trace.y),
        ),
    )
    .toEqual([
      [1, 1, null, 0.9],
      [1, null, null, 0.9],
    ]);
  await page.getByText('Freshness and clock ledger', { exact: true }).click();
  await expect(
    page.getByRole('cell', { name: 'held_or_reset_sequence', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('cell', { name: 'invalid_packet', exact: true })).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Measured reference and declared uncertainty' }),
  ).toBeVisible();
  await expect(page.getByRole('img', { name: '3D trajectory and anchors' })).toHaveCount(0);
});

test('factor labels expose exploratory overrides and distinguish incompatible protocol identities', async ({
  page,
  request,
}) => {
  const catalog = await (await request.get('/api/catalog')).json();
  const baseline = {
    status: 'completed',
    config: {
      ...catalog.default_config,
      method: 'M3',
      dataset_id: 'roboloc-g',
      session_id: 'circle',
      calibration_id: 'test-only-calibration',
      factors: { propagation: 'cv', radar: false, gating: false },
    },
    metrics: { position_rmse_m: 1 },
    provenance: {
      investigation_id: 'test-only-investigation',
      protocol_hash: 'test-only-protocol',
      result_kind: 'exploratory_investigation',
      scientific_source_hash: 'test-only-source',
      source_revision: 'test-only-revision',
      raw_sources: [{ name: 'test-only.zip', sha256: 'test-only-hash' }],
      session: { calibration_hash: 'test-only-calibration-hash', target: 'gantry_origin' },
    },
  };
  const left = { ...baseline, run_id: 'test-only-left' };
  const right = {
    ...baseline,
    run_id: 'test-only-right',
    config: { ...baseline.config, factors: { propagation: 'cv', radar: false, gating: true } },
    provenance: { ...baseline.provenance },
  };
  await page.route('**/api/runs', (route) => route.fulfill({ json: { runs: [left, right] } }));
  await page.route('**/api/runs/test-only-left', (route) => route.fulfill({ json: left }));
  await page.route('**/api/runs/test-only-right', (route) => route.fulfill({ json: right }));
  const trajectory = {
    times: [0, 1],
    positions: [
      [0, 0, 0],
      [1, 0, 0],
    ],
    reference_positions: [
      [0, 0, 0],
      [0, 0, 0],
    ],
    position_errors: [0, 1],
  };
  await page.route('**/api/runs/test-only-*/trajectory?*', (route) =>
    route.fulfill({ json: trajectory }),
  );
  await page.goto('/?tab=comparison&run=test-only-left&compare=test-only-right');
  await expect(
    page.getByText('Controlled exploratory factor comparison', { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole('columnheader', {
      name: 'A: CV / radar off / gate off (exploratory)',
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole('columnheader', {
      name: 'B: CV / radar off / gate on (exploratory)',
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator('.comparison .notice')).toHaveCount(0);
  right.provenance.protocol_hash = 'test-only-other-protocol';
  await page.getByRole('button', { name: 'Refresh records', exact: true }).click();
  await expect(page.locator('.comparison .notice')).toContainText('Registered protocols differ.');
  await expect(
    page.getByText('Controlled exploratory factor comparison', { exact: false }),
  ).toHaveCount(0);
});
