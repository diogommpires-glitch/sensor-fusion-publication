import type { FormEvent } from 'react';
import { useState } from 'react';
import { request } from './api';
import { Empty, methodLabel, Status } from './components';
import { type Catalog, isActive, type Run, type RunConfig } from './types';

interface RadarCalibration {
  certified: boolean;
  reason?: string;
  selected_clustering?: { radius_m: number; min_samples: number };
}

export default function Experiment({
  catalog,
  runs,
  onRun,
  onReplay,
}: {
  catalog: Catalog;
  runs: Run[];
  onRun: (id: string) => void;
  onReplay: (id: string) => void;
}) {
  const [draft, setDraft] = useState<RunConfig>(() => structuredClone(catalog.default_config));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dataset = catalog.datasets.find((item) => item.id === draft.dataset_id);
  const session = dataset?.sessions.find((item) => item.id === draft.session_id);
  const sourceBlocked = dataset?.status === 'blocked' || session?.status === 'blocked';
  const protectedSession =
    session?.status === 'protected' || ['final', 'test'].includes(session?.partition ?? '');
  const calibration = catalog.calibrations.find((item) => item.id === draft.calibration_id);
  const certified = Array.isArray(calibration?.certified_modalities)
    ? (calibration.certified_modalities as string[])
    : [];
  const required = catalog.methods.find((method) => method.id === draft.method)?.modalities ?? [];
  const missing = required.filter((modality) => !certified.includes(modality));
  const radarCalibration = (calibration?.radars as Record<string, RadarCalibration> | undefined)?.[
    draft.radar_sensor
  ];
  let calibrationReason = '';
  if (draft.dataset_id !== 'fixture') {
    if (!calibration)
      calibrationReason = 'Choose an explicit development calibration for recorded measurements.';
    else if (missing.length)
      calibrationReason = `This calibration does not certify ${missing.join(', ').toUpperCase()} required by ${draft.method}. Choose a certified method or calibration.`;
    else if (required.includes('radar') && !radarCalibration?.certified)
      calibrationReason = `Radar ${draft.radar_sensor} is not certified. ${radarCalibration?.reason ?? 'Reviewed development annotations and effective calibration are required.'}`;
    else if (required.includes('radar') && !radarCalibration?.selected_clustering)
      calibrationReason = `Radar ${draft.radar_sensor} has no frozen clustering parameters in this calibration.`;
  }
  const blocked = sourceBlocked || protectedSession || !!calibrationReason;
  const active = runs.find((run) => isActive(run.status));
  const set = <K extends keyof RunConfig>(key: K, value: RunConfig[K]) =>
    setDraft((previous) => ({ ...previous, [key]: value }));
  const selectCalibration = (
    calibrationId: string | null,
    radarSensor: RunConfig['radar_sensor'],
  ) =>
    setDraft((previous) => {
      const next = { ...previous, calibration_id: calibrationId, radar_sensor: radarSensor };
      const selected = catalog.calibrations.find((item) => item.id === calibrationId);
      const clustering = (selected?.radars as Record<string, RadarCalibration> | undefined)?.[
        radarSensor
      ]?.selected_clustering;
      if (previous.dataset_id !== 'fixture' && clustering) {
        next.radar_cluster_radius_m = clustering.radius_m;
        next.radar_min_samples = clustering.min_samples;
      }
      return next;
    });
  const fault = <K extends keyof RunConfig['fault']>(key: K, value: RunConfig['fault'][K]) =>
    setDraft((previous) => ({ ...previous, fault: { ...previous.fault, [key]: value } }));
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      const result = await request<{ run_id: string }>('/api/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft),
      });
      onRun(result.run_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPending(false);
    }
  }
  const number = (
    key:
      | 'q_cv'
      | 'acceleration_noise'
      | 'bias_diffusion'
      | 'gate_probability'
      | 'wls_window_s'
      | 'radar_cluster_radius_m'
      | 'radar_min_samples'
      | 'output_hz'
      | 'duration_s'
      | 'seed',
    label: string,
    min: number,
    max: number,
    step: number | 'any' = 'any',
  ) => (
    <label className="field" key={key}>
      {label}
      <input
        name={key}
        type="number"
        required
        disabled={
          draft.dataset_id !== 'fixture' &&
          !!radarCalibration?.selected_clustering &&
          (key === 'radar_cluster_radius_m' || key === 'radar_min_samples')
        }
        min={min}
        max={max}
        step={step}
        value={Number.isNaN(draft[key]) ? '' : draft[key]}
        onChange={(event) => set(key, event.target.valueAsNumber)}
      />
    </label>
  );
  return (
    <div className="experiment-grid">
      <form onSubmit={submit} className="experiment-form">
        <section className="form-section">
          <div className="section-heading">
            <h2>Build an experiment</h2>
            <span className="muted">Exploratory run</span>
          </div>
          <p className="intro">
            Choose observations and an estimator. The engine preserves the resolved configuration.
          </p>
          <div className="fields two">
            <label className="field">
              Dataset
              <select
                name="dataset_id"
                aria-label="Dataset"
                value={draft.dataset_id}
                onChange={(event) => {
                  const id = event.target.value as RunConfig['dataset_id'];
                  const next = catalog.datasets.find((item) => item.id === id);
                  setDraft((previous) => ({
                    ...previous,
                    dataset_id: id,
                    session_id: next?.sessions[0]?.id ?? previous.session_id,
                    method: id === 'miluv' ? 'M2' : previous.method,
                    calibration_id: null,
                  }));
                }}
              >
                {catalog.datasets.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Session
              <select
                name="session_id"
                value={draft.session_id}
                onChange={(event) => set('session_id', event.target.value)}
              >
                {dataset?.sessions.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label} ({item.partition})
                  </option>
                ))}
              </select>
            </label>
          </div>
          {draft.dataset_id === 'fixture' && (
            <p className="notice">
              Synthetic validation fixture, not a real-data study. Errors validate behaviour under
              this generated model.
            </p>
          )}
          {sourceBlocked && (
            <p className="error" role="status">
              Dataset unavailable.{' '}
              {dataset?.reason ||
                'This session has not passed the source-access and calibration gates.'}
            </p>
          )}
          {protectedSession && (
            <p className="error" role="status">
              Protected final session. Use the frozen study protocol after validation and parameter
              registration.
            </p>
          )}
          <fieldset className="methods">
            <legend>Estimator</legend>
            {catalog.methods.map((method) => (
              <label className="method" key={method.id}>
                <input
                  type="radio"
                  name="method"
                  value={method.id}
                  checked={draft.method === method.id}
                  disabled={draft.dataset_id === 'miluv' && !['M1', 'M2'].includes(method.id)}
                  onChange={() => set('method', method.id)}
                />
                <span className="method-id">{method.id}</span>
                <span>
                  <strong>{method.name}</strong>
                  <small>{method.modalities.join(' + ').toUpperCase()}</small>
                </span>
              </label>
            ))}
          </fieldset>
          <div className="fields two">
            <label className="field">
              Radar substitution
              <select
                name="radar_sensor"
                value={draft.radar_sensor}
                onChange={(event) =>
                  selectCalibration(
                    draft.calibration_id,
                    event.target.value as RunConfig['radar_sensor'],
                  )
                }
              >
                <option>AOP</option>
                <option>ISK0</option>
                <option>ISK1</option>
              </select>
              <small>One radar at a time, no assumed independence.</small>
            </label>
            <label className="field">
              Calibration
              <select
                name="calibration_id"
                value={draft.calibration_id ?? ''}
                onChange={(event) =>
                  selectCalibration(event.target.value || null, draft.radar_sensor)
                }
              >
                <option value="">
                  {draft.dataset_id === 'fixture'
                    ? 'Declared synthetic geometry'
                    : 'Choose a development calibration'}
                </option>
                {catalog.calibrations
                  .filter((item) => !item.dataset_id || item.dataset_id === draft.dataset_id)
                  .map((item) => (
                    <option key={String(item.id)} value={String(item.id)}>
                      {String(item.name ?? item.id)} (
                      {Array.isArray(item.certified_modalities)
                        ? item.certified_modalities.join(' + ')
                        : 'certification unavailable'}
                      )
                    </option>
                  ))}
              </select>
            </label>
          </div>
          {draft.dataset_id !== 'fixture' && (
            <p className={calibrationReason ? 'notice' : 'caption'} role="status">
              {calibrationReason ||
                `${draft.method} observation classes are certified by the selected development calibration. The engine still verifies source hashes and frames.`}
            </p>
          )}
        </section>
        <section className="form-section">
          <details>
            <summary>Physical settings and reproducibility</summary>
            <p className="muted">
              Initial values come from the engine contract. Changes apply to a new run.
            </p>
            {draft.dataset_id !== 'fixture' && radarCalibration?.selected_clustering && (
              <p className="caption">
                Radar clustering is frozen by the selected development calibration and radar
                substitution.
              </p>
            )}
            <div className="fields two">
              {number('q_cv', 'CV acceleration spectral density (m²/s³)', 0, 100)}
              {number('acceleration_noise', 'Acceleration noise (m²/s³)', 0.0000000001, 100)}
              {number('bias_diffusion', 'Bias diffusion (m²/s⁵)', 0, 1)}
              {number(
                'gate_probability',
                'Innovation gate probability',
                0.5000000001,
                0.9999999999,
              )}
              {number('wls_window_s', 'WLS window (s)', 0.00001, 1)}
              {number('radar_cluster_radius_m', 'Radar clustering radius (m)', 0.00001, 2)}
              {number('radar_min_samples', 'Radar cluster minimum detections', 1, 100, 1)}
              {number('output_hz', 'Evaluation output rate (Hz)', 1, 100)}
              {number('duration_s', 'Fixture duration (s)', 5, 300)}
              {number('seed', 'Fixture seed', 0, 4294967295, 1)}
            </div>
          </details>
        </section>
        <section className="form-section">
          <details>
            <summary>Inject a labelled synthetic fault</summary>
            <div className="fields two">
              <label className="field">
                Fault
                <select
                  name="fault_kind"
                  value={draft.fault.kind}
                  onChange={(event) =>
                    fault('kind', event.target.value as RunConfig['fault']['kind'])
                  }
                >
                  <option value="none">No injected fault</option>
                  <option value="dropout">Absolute-positioning dropout</option>
                  <option value="range_bias">Positive range bias</option>
                  <option value="delay">Arrival delay</option>
                  <option value="imu_bias">Residual IMU bias</option>
                  <option value="radar_shift">Radar centroid displacement</option>
                </select>
              </label>
              <label className="field">
                Level (
                {draft.fault.kind === 'delay' || draft.fault.kind === 'dropout'
                  ? 's'
                  : draft.fault.kind === 'imu_bias'
                    ? 'm/s²'
                    : 'm'}
                )
                <input
                  name="fault_level"
                  type="number"
                  min="0"
                  max="10"
                  step="any"
                  required
                  value={draft.fault.level}
                  onChange={(event) => fault('level', event.target.valueAsNumber)}
                />
              </label>
              <label className="field">
                Schedule seed
                <input
                  name="fault_seed"
                  type="number"
                  min="0"
                  max="4294967295"
                  required
                  value={draft.fault.seed}
                  onChange={(event) => fault('seed', event.target.valueAsNumber)}
                />
              </label>
              <label className="field">
                Start (s), blank for seeded schedule
                <input
                  name="fault_start"
                  type="number"
                  min="0"
                  step="any"
                  value={draft.fault.start_s ?? ''}
                  onChange={(event) =>
                    fault('start_s', event.target.value === '' ? null : event.target.valueAsNumber)
                  }
                />
              </label>
              <label className="field">
                Episode duration (s)
                <input
                  name="fault_duration"
                  type="number"
                  min="0.00001"
                  max="60"
                  required
                  step="any"
                  value={draft.fault.duration_s}
                  onChange={(event) => fault('duration_s', event.target.valueAsNumber)}
                />
              </label>
              <label className="field">
                Declared axis
                <select
                  name="fault_axis"
                  value={draft.fault.axis}
                  onChange={(event) => fault('axis', Number(event.target.value) as 0 | 1 | 2)}
                >
                  <option value="0">x</option>
                  <option value="1">y</option>
                  <option value="2">z</option>
                </select>
              </label>
              <label className="field">
                Sensor ID, blank for engine selection
                <input
                  name="fault_sensor"
                  value={draft.fault.sensor_id ?? ''}
                  onChange={(event) => fault('sensor_id', event.target.value || null)}
                />
              </label>
            </div>
            <p className="muted">
              Injected corruption does not establish real NLOS frequencies or RF behaviour.
            </p>
          </details>
        </section>
        <div className="submit-row">
          <button
            type="submit"
            className="primary"
            disabled={pending || blocked || !!active}
            aria-busy={pending}
          >
            {pending ? 'Starting experiment…' : 'Run experiment'}
          </button>
          <span className="muted">
            {active
              ? 'One local worker is busy. Cancel or wait for the active run.'
              : 'Local CPU worker. Results are saved on this machine.'}
          </span>
        </div>
        {error && (
          <p className="error" role="alert">
            Run was not started. {error}
          </p>
        )}
      </form>
      <aside className="run-ledger">
        <h2>Run ledger</h2>
        <p className="muted">Completed and failed records remain visible.</p>
        {runs.length ? (
          <div className="ledger-list">
            {runs.map((run) => (
              <button
                type="button"
                key={run.run_id}
                className="ledger-row"
                onClick={() => onReplay(run.run_id)}
              >
                <span className="ledger-top">
                  <strong>
                    {methodLabel(run.config)} / {run.config.session_id}
                  </strong>
                  <Status value={run.status} />
                </span>
                <span>
                  {run.config.dataset_id === 'fixture'
                    ? 'Synthetic fixture'
                    : run.config.dataset_id}{' '}
                  / {run.config.radar_sensor}
                </span>
                <small>{run.run_id.slice(0, 12)}</small>
              </button>
            ))}
          </div>
        ) : (
          <Empty title="No experiments yet">
            <p>Run an estimator to create a persistent result and open its replay.</p>
          </Empty>
        )}
        <div className="ledger-note">
          <h3>Evaluation boundary</h3>
          <p>
            Reference positions enter evaluation only. They are never estimator inputs or initial
            states.
          </p>
        </div>
      </aside>
    </div>
  );
}
