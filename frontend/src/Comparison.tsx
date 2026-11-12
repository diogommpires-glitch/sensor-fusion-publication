import type { Data } from 'plotly.js';
import { useMemo } from 'react';
import { useResource } from './api';
import { Empty, formatNumber, LoadState, methodLabel, RunSelect } from './components';
import Plot, { plotColour } from './Plot';
import { positionErrors } from './Replay';
import type { Run, Trajectory } from './types';

function ComparePlot({
  left,
  right,
  leftRun,
  rightRun,
}: {
  left: Trajectory;
  right: Trajectory;
  leftRun: Run;
  rightRun: Run;
}) {
  const data = useMemo<Data[]>(
    () =>
      [left, right].map((trajectory, index) => ({
        type: 'scatter',
        mode: 'lines',
        name: `${methodLabel(index === 0 ? leftRun.config : rightRun.config)} / ${index === 0 ? leftRun.run_id.slice(0, 8) : rightRun.run_id.slice(0, 8)}`,
        x: trajectory.times,
        y: positionErrors(trajectory),
        line: {
          color: plotColour(index === 0 ? 'estimate' : 'reference'),
          width: 2,
          dash: index === 0 ? 'solid' : 'dash',
        },
        connectgaps: false,
      })),
    [left, right, leftRun, rightRun],
  );
  return (
    <section className="plot-panel comparison-plot">
      <h2>Paired position errors</h2>
      <Plot
        data={data}
        label="Position errors for the two selected runs"
        layout={{
          xaxis: { title: { text: 'Acquisition time (s)' }, gridcolor: plotColour('rule') },
          yaxis: {
            title: { text: 'Position error (m)' },
            gridcolor: plotColour('rule'),
            rangemode: 'tozero',
          },
        }}
      />
    </section>
  );
}

export default function Comparison({
  runs,
  leftId,
  rightId,
  setLeft,
  setRight,
  revision,
}: {
  runs: Run[];
  leftId: string;
  rightId: string;
  setLeft: (id: string) => void;
  setRight: (id: string) => void;
  revision: number;
}) {
  const left = useResource<Run>(
    leftId ? `/api/runs/${encodeURIComponent(leftId)}` : null,
    revision,
  );
  const right = useResource<Run>(
    rightId ? `/api/runs/${encodeURIComponent(rightId)}` : null,
    revision,
  );
  const leftTrajectory = useResource<Trajectory>(
    left.data?.status === 'completed'
      ? `/api/runs/${encodeURIComponent(leftId)}/trajectory?max_points=2000`
      : null,
    revision,
  );
  const rightTrajectory = useResource<Trajectory>(
    right.data?.status === 'completed'
      ? `/api/runs/${encodeURIComponent(rightId)}/trajectory?max_points=2000`
      : null,
    revision,
  );
  const warnings: string[] = [];
  const a = left.data;
  const b = right.data;
  const factorComparison = !!(a?.config.factors || b?.config.factors);
  const sameInvestigation = !!(
    a?.provenance?.investigation_id &&
    a.provenance.investigation_id === b?.provenance?.investigation_id &&
    a.provenance.protocol_hash === b?.provenance?.protocol_hash
  );
  if (a && b) {
    if (a.run_id === b.run_id)
      warnings.push(
        'The same run is selected twice. Choose a second experiment for a paired comparison.',
      );
    if (a.config.dataset_id !== b.config.dataset_id || a.config.session_id !== b.config.session_id)
      warnings.push('Different dataset or session: this is not a paired trajectory comparison.');
    if (a.provenance?.protocol_hash !== b.provenance?.protocol_hash)
      warnings.push(
        'Registered protocols differ. This is not a single-protocol factor comparison.',
      );
    if (a.provenance?.result_kind !== b.provenance?.result_kind)
      warnings.push(
        'Result kinds differ. Exploratory investigations and frozen final evidence have separate scientific roles.',
      );
    if (factorComparison && !sameInvestigation)
      warnings.push(
        'Factor conditions do not share an identified exploratory investigation and protocol.',
      );
    const aSession = a.provenance?.session as Record<string, unknown> | undefined;
    const bSession = b.provenance?.session as Record<string, unknown> | undefined;
    if (
      a.config.calibration_id !== b.config.calibration_id ||
      aSession?.calibration_hash !== bSession?.calibration_hash
    )
      warnings.push('Calibration differs. This is not an otherwise identical method ablation.');
    if (
      (a.config.dataset_id !== 'fixture' || b.config.dataset_id !== 'fixture') &&
      (!aSession?.calibration_hash || !bSession?.calibration_hash)
    )
      warnings.push(
        'A recorded-data calibration hash is unavailable. Calibration compatibility cannot be established.',
      );
    const settings = [
      'seed',
      'duration_s',
      'output_hz',
      'q_cv',
      'acceleration_noise',
      'bias_diffusion',
      'gate_probability',
      'wls_window_s',
      'radar_sensor',
      'radar_cluster_radius_m',
      'radar_min_samples',
    ] as const;
    const changedSettings = settings.filter((key) => a.config[key] !== b.config[key]);
    if (changedSettings.length)
      warnings.push(
        `Physical, generation or evaluation settings differ: ${changedSettings.join(', ')}. Interpret this as a comparison of multiple settings.`,
      );
    const changedData = ['source_version', 'target', 'time_origin_s'].filter(
      (key) => JSON.stringify(aSession?.[key]) !== JSON.stringify(bSession?.[key]),
    );
    if (changedData.length)
      warnings.push(`Recorded observation provenance differs: ${changedData.join(', ')}.`);
    if (JSON.stringify(a.provenance?.raw_sources) !== JSON.stringify(b.provenance?.raw_sources))
      warnings.push(
        'Raw source archive hashes differ. Matching dataset and session names do not establish identical observations.',
      );
    if (
      (a.config.dataset_id !== 'fixture' || b.config.dataset_id !== 'fixture') &&
      (!a.provenance?.raw_sources || !b.provenance?.raw_sources)
    )
      warnings.push(
        'A raw source archive fingerprint is unavailable. Recorded observation compatibility cannot be established.',
      );
    if (!a.provenance?.scientific_source_hash || !b.provenance?.scientific_source_hash)
      warnings.push(
        'A scientific source fingerprint is unavailable. Matching revision labels alone do not establish identical code.',
      );
    else if (a.provenance.scientific_source_hash !== b.provenance.scientific_source_hash)
      warnings.push(
        'Scientific source fingerprints differ. Uncommitted implementation changes may contribute to the observed difference.',
      );
    if (a.provenance?.source_revision !== b.provenance?.source_revision)
      warnings.push(
        'Scientific source revisions differ. Changes to implementation may contribute to the observed difference.',
      );
    if (a.status !== 'completed' || b.status !== 'completed')
      warnings.push(
        'Both runs must complete before trajectory comparison. Failed and cancelled runs are retained, not scored as zero.',
      );
  }
  const metrics = [
    ['3D position RMSE (m)', 'position_rmse_m'],
    ['Median error (m)', 'median_error_m'],
    ['95th percentile error (m)', 'p95_error_m'],
    ['Availability (fraction)', 'availability'],
    ['Reference coverage (fraction)', 'reference_coverage'],
    ['Scored samples', 'scored_samples'],
    ['Empirical 95% position coverage (fraction)', 'position_coverage_95'],
    ['Processing time (s)', 'processing_s'],
    ['Real-time factor', 'real_time_factor'],
    ['Accepted updates', 'accepted_updates'],
    ['Rejected updates', 'rejected_updates'],
  ];
  return (
    <div className="comparison">
      <p className="intro">
        Inspect errors and failures together. Synthetic fixtures cannot establish the primary study
        hypothesis.
      </p>
      <div className="compare-selectors">
        <RunSelect runs={runs} selected={leftId} onChange={setLeft} label="Run A" />
        <RunSelect runs={runs} selected={rightId} onChange={setRight} label="Run B" />
      </div>
      {!leftId || !rightId ? (
        <Empty title="Select two runs" level={2}>
          <p>Create at least two experiments and select their saved records above.</p>
        </Empty>
      ) : (
        <LoadState resource={left}>
          {() => (
            <LoadState resource={right}>
              {() =>
                a &&
                b && (
                  <>
                    {factorComparison && sameInvestigation && (
                      <p className="provenance-line">
                        Controlled exploratory factor comparison within one registered protocol.
                        Inspect factor labels and their frozen propagation settings.
                      </p>
                    )}
                    {warnings.length > 0 && (
                      <div className="notice" role="status">
                        <strong>Comparison limitations</strong>
                        <ul>
                          {warnings.map((warning) => (
                            <li key={warning}>{warning}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {(a.config.dataset_id === 'fixture' || b.config.dataset_id === 'fixture') && (
                      <p className="provenance-line">
                        Synthetic validation results. No real-data accuracy claim.
                      </p>
                    )}
                    <table className="comparison-table">
                      <thead>
                        <tr>
                          <th>Measured outcome</th>
                          <th>A: {methodLabel(a.config)}</th>
                          <th>B: {methodLabel(b.config)}</th>
                          <th>B minus A</th>
                        </tr>
                      </thead>
                      <tbody>
                        {metrics.map(([label, key]) => {
                          const av = a.metrics?.[key];
                          const bv = b.metrics?.[key];
                          return (
                            <tr key={key}>
                              <th scope="row">{label}</th>
                              <td>{formatNumber(av)}</td>
                              <td>{formatNumber(bv)}</td>
                              <td>
                                {formatNumber(
                                  typeof av === 'number' && typeof bv === 'number' ? bv - av : null,
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    {a.status === 'completed' && b.status === 'completed' && (
                      <LoadState resource={leftTrajectory}>
                        {(lt) => (
                          <LoadState resource={rightTrajectory}>
                            {(rt) => <ComparePlot left={lt} right={rt} leftRun={a} rightRun={b} />}
                          </LoadState>
                        )}
                      </LoadState>
                    )}
                    <details>
                      <summary>Resolved configurations</summary>
                      <div className="config-pair">
                        <pre>{JSON.stringify(a.config, null, 2)}</pre>
                        <pre>{JSON.stringify(b.config, null, 2)}</pre>
                      </div>
                    </details>
                  </>
                )
              }
            </LoadState>
          )}
        </LoadState>
      )}
    </div>
  );
}
