import { expect, type Page, test } from '@playwright/test';
import type { Catalog, Run, RunConfig, Trajectory } from '../src/types';

const config: RunConfig = {
  dataset_id: 'fixture',
  session_id: 'helix',
  method: 'M2',
  radar_sensor: 'AOP',
  calibration_id: null,
  q_cv: 0.1,
  acceleration_noise: 0.01,
  bias_diffusion: 0.000001,
  gate_probability: 0.999,
  wls_window_s: 0.2,
  radar_cluster_radius_m: 0.2,
  radar_min_samples: 3,
  output_hz: 20,
  duration_s: 300,
  seed: 7,
  fault: {
    kind: 'none',
    level: 0,
    seed: 0,
    start_s: null,
    duration_s: 2,
    axis: 0,
    sensor_id: null,
  },
};

function trajectory(samples: number, total: number): Trajectory {
  return {
    times: Array.from({ length: samples }, (_, index) => index / 20),
    position_errors: Array(samples).fill(0.2),
    positions: [],
    reference_positions: [],
    covariance_diagonal: [],
    position_covariances: [],
    confidence_ellipsoid_axes: [],
    confidence_ellipse_xy_axes: [],
    confidence_ellipse_xz_axes: [],
    confidence_probability: 0.95,
    velocities: [],
    biases: [],
    anchors: [],
    decimated: samples < total,
    total_points: total,
  };
}

async function comparison(page: Page, leftTrace = trajectory(101, 101), rightTrace = leftTrace) {
  const provenance = {
    scientific_source_hash: 'fixture-source',
    source_revision: 'fixture-revision',
  };
  const runs: Run[] = ['left', 'right'].map((run_id, index) => ({
    run_id,
    status: 'completed',
    config: { ...config, fault: { ...config.fault } },
    metrics: { position_rmse_m: index === 0 ? 0.1 : 0.5 },
    provenance,
  }));
  const catalog: Catalog = {
    datasets: [
      {
        id: 'fixture',
        name: 'Synthetic comparison presentation fixture',
        status: 'ready',
        sessions: [{ id: 'helix', label: 'helix', partition: 'synthetic', status: 'ready' }],
      },
    ],
    methods: [{ id: 'M2', name: 'UWB constant-velocity EKF', modalities: ['uwb'] }],
    calibrations: [],
    limitations: [],
    default_config: config,
  };
  // Controlled API payloads test presentation; these are not executed scientific runs.
  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const payloads: Record<string, unknown> = {
      '/api/health': { status: 'ok', version: '0.2.0' },
      '/api/catalog': catalog,
      '/api/runs': { runs },
      '/api/runs/left': runs[0],
      '/api/runs/right': runs[1],
      '/api/runs/left/artifacts': { artifacts: [] },
      '/api/runs/left/trajectory': leftTrace,
      '/api/runs/right/trajectory': rightTrace,
    };
    if (!(pathname in payloads)) throw new Error(`Unexpected fixture request: ${pathname}`);
    await route.fulfill({ json: payloads[pathname] });
  });
  return runs;
}

test('comparison discloses fault differences and retains intentional fault comparisons', async ({
  page,
}) => {
  const runs = await comparison(page);
  runs[1].config.fault = { ...config.fault, kind: 'delay', level: 0.25 };
  await page.goto('/?tab=comparison&run=left&compare=right');
  const notice = page.getByRole('status').filter({ hasText: 'Comparison limitations' });
  await expect(notice).toContainText('Fault settings or schedules differ: kind, level.');
  await expect(
    page.getByRole('img', { name: 'Position errors for the two selected runs' }),
  ).toBeVisible();
  await expect(page.locator('.comparison-table tbody tr').first()).toContainText('0.4');

  runs[0].config.fault = { ...runs[1].config.fault };
  await page.getByRole('button', { name: 'Refresh records', exact: true }).click();
  await expect(notice).toHaveCount(0);
});

test('comparison labels each rendered trace and keeps saved metrics unchanged', async ({
  page,
}) => {
  await comparison(page, trajectory(2000, 6001), trajectory(1000, 4001));
  await page.goto('/?tab=comparison&run=left&compare=right');
  const caption = page.locator('.comparison-plot .caption');
  await expect(caption).toContainText('Run A: 2,000 of 6,001 points (decimated).');
  await expect(caption).toContainText('Run B: 1,000 of 4,001 points (decimated).');
  await expect(caption).toContainText('Saved metrics use all eligible data.');
  await expect(page.locator('.comparison-table tbody tr').first().locator('td')).toHaveText([
    '0.1',
    '0.5',
    '0.4',
  ]);
});

test('mobile comparison values can be reached by keyboard without page overflow', async ({
  page,
}) => {
  await comparison(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/?tab=comparison&run=left&compare=right');
  const region = page.getByRole('region', { name: 'Comparison outcomes', exact: true });
  await expect(region).toBeVisible();
  await region.focus();
  await expect(region).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => region.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
  for (let step = 0; step < 8; step++) await page.keyboard.press('ArrowRight');
  await expect
    .poll(() =>
      region.evaluate((element) => {
        const edge = element.getBoundingClientRect();
        const delta = element.querySelector('tbody td:last-child')?.getBoundingClientRect();
        return !!delta && delta.left >= edge.left && delta.right <= edge.right + 1;
      }),
    )
    .toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
