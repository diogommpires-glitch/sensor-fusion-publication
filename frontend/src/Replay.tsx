import type { Data, Layout } from 'plotly.js';
import { useEffect, useMemo, useState } from 'react';
import { useResource } from './api';
import {
  Empty,
  formatNumber,
  LoadState,
  MetricStrip,
  RecordTable,
  TableScroll,
} from './components';
import Plot, { plotColour } from './Plot';
import {
  type Diagnostics,
  finiteVector,
  type RadarFrame,
  type Run,
  type Trajectory,
  type Vector,
  type VectorSample,
} from './types';

export function positionErrors(trajectory: Trajectory): (number | null)[] {
  return trajectory.position_errors.map((value) =>
    typeof value === 'number' && Number.isFinite(value) ? value : null,
  );
}

function coordinates(vectors: VectorSample[], axis: number) {
  return vectors.map((vector) => (finiteVector(vector) ? vector[axis] : null));
}

function finiteMatrix(matrix: number[][] | null | undefined, size: number): matrix is number[][] {
  return (
    Array.isArray(matrix) &&
    matrix.length === size &&
    matrix.every((row) => Array.isArray(row) && row.length === size && row.every(Number.isFinite))
  );
}

function confidenceTrace(
  trajectory: Trajectory,
  cursor: number,
  projection: '3d' | 'xy' | 'xz',
): Data | null {
  const centre = trajectory.positions[cursor];
  if (!finiteVector(centre) || !Number.isFinite(trajectory.confidence_probability)) return null;
  const probability = `${formatNumber(trajectory.confidence_probability * 100, 1)}%`;
  if (projection !== '3d') {
    const axes =
      projection === 'xy'
        ? trajectory.confidence_ellipse_xy_axes[cursor]
        : trajectory.confidence_ellipse_xz_axes[cursor];
    if (!finiteMatrix(axes, 2)) return null;
    const angles = Array.from({ length: 65 }, (_, index) => (index * 2 * Math.PI) / 64);
    const yAxis = projection === 'xy' ? 1 : 2;
    return {
      type: 'scatter',
      mode: 'lines',
      name: `Nominal ${probability} marginal ellipse`,
      x: angles.map(
        (angle) => centre[0] + axes[0][0] * Math.cos(angle) + axes[0][1] * Math.sin(angle),
      ),
      y: angles.map(
        (angle) => centre[yAxis] + axes[1][0] * Math.cos(angle) + axes[1][1] * Math.sin(angle),
      ),
      line: { color: plotColour('estimate'), width: 1.5, dash: 'dot' },
      hoverinfo: 'skip',
    };
  }
  const axes = trajectory.confidence_ellipsoid_axes[cursor];
  if (!finiteMatrix(axes, 3)) return null;
  const points: (Vector | null)[] = [];
  const transform = (unit: Vector): Vector =>
    centre.map(
      (value, row) => value + axes[row].reduce((sum, axis, column) => sum + axis * unit[column], 0),
    ) as Vector;
  // These circles sample a unit sphere; the engine supplies the full statistical axis transform.
  for (let meridian = 0; meridian < 8; meridian++) {
    const longitude = (meridian * Math.PI) / 8;
    for (let segment = 0; segment <= 64; segment++) {
      const angle = (segment * 2 * Math.PI) / 64;
      points.push(
        transform([
          Math.cos(angle) * Math.cos(longitude),
          Math.cos(angle) * Math.sin(longitude),
          Math.sin(angle),
        ]),
      );
    }
    points.push(null);
  }
  for (const latitude of [-Math.PI / 3, -Math.PI / 6, 0, Math.PI / 6, Math.PI / 3]) {
    for (let segment = 0; segment <= 64; segment++) {
      const longitude = (segment * 2 * Math.PI) / 64;
      points.push(
        transform([
          Math.cos(latitude) * Math.cos(longitude),
          Math.cos(latitude) * Math.sin(longitude),
          Math.sin(latitude),
        ]),
      );
    }
    points.push(null);
  }
  return {
    type: 'scatter3d',
    mode: 'lines',
    name: `Nominal ${probability} position ellipsoid`,
    x: coordinates(points, 0),
    y: coordinates(points, 1),
    z: coordinates(points, 2),
    line: { color: plotColour('estimate'), width: 1 },
    opacity: 0.7,
    hoverinfo: 'skip',
    connectgaps: false,
  };
}

function SpatialPlot({
  trajectory,
  cursor,
  projection,
}: {
  trajectory: Trajectory;
  cursor: number;
  projection: '3d' | 'xy' | 'xz';
}) {
  const data = useMemo<Data[]>(() => {
    const is3d = projection === '3d';
    const yAxis = projection === 'xz' ? 2 : 1;
    const line = (
      positions: VectorSample[],
      name: string,
      colour: string,
      dash: 'solid' | 'dash',
    ): Data => ({
      type: is3d ? 'scatter3d' : 'scatter',
      mode: 'lines',
      name,
      x: coordinates(positions, 0),
      y: coordinates(positions, yAxis),
      ...(is3d ? { z: coordinates(positions, 2) } : {}),
      line: { color: colour, width: is3d ? 4 : 2, dash },
      connectgaps: false,
      hovertemplate: `${name}<br>x %{x:.3f} m<br>${projection === 'xz' ? 'z' : 'y'} %{y:.3f} m${is3d ? '<br>z %{z:.3f} m' : ''}<extra></extra>`,
    });
    const current = trajectory.positions[cursor];
    const anchors = trajectory.anchors;
    const traces: Data[] = [
      line(trajectory.reference_positions, 'Evaluation reference', plotColour('reference'), 'dash'),
      line(trajectory.positions, 'Estimate', plotColour('estimate'), 'solid'),
    ];
    traces.push({
      type: is3d ? 'scatter3d' : 'scatter',
      mode: 'markers',
      name: 'Anchors',
      x: anchors.map((anchor) => anchor.position[0]),
      y: anchors.map((anchor) => anchor.position[yAxis]),
      ...(is3d ? { z: anchors.map((anchor) => anchor.position[2]) } : {}),
      text: anchors.map((anchor) => anchor.id),
      marker: { size: 5, color: plotColour('muted'), symbol: 'diamond' },
      hovertemplate: '%{text}<extra>Anchor</extra>',
    });
    const confidence = confidenceTrace(trajectory, cursor, projection);
    if (confidence) traces.push(confidence);
    if (finiteVector(current))
      traces.push({
        type: is3d ? 'scatter3d' : 'scatter',
        mode: 'markers',
        name: 'Time cursor',
        x: [current[0]],
        y: [current[yAxis]],
        ...(is3d ? { z: [current[2]] } : {}),
        marker: {
          size: 8,
          color: plotColour('estimate'),
          line: { color: plotColour('ink'), width: 2 },
        },
        hovertemplate: `${formatNumber(trajectory.times[cursor])} s<extra>Selected estimate</extra>`,
      });
    return traces;
  }, [trajectory, cursor, projection]);
  const layout = useMemo<Partial<Layout>>(
    () =>
      projection === '3d'
        ? {
            scene: {
              xaxis: {
                title: { text: 'x (m)' },
                gridcolor: plotColour('rule'),
                zerolinecolor: plotColour('rule'),
              },
              yaxis: {
                title: { text: 'y (m)' },
                gridcolor: plotColour('rule'),
                zerolinecolor: plotColour('rule'),
              },
              zaxis: {
                title: { text: 'z (m)' },
                gridcolor: plotColour('rule'),
                zerolinecolor: plotColour('rule'),
              },
              aspectmode: 'data' as const,
              camera: { eye: { x: 1.9, y: 1.8, z: 1.3 } },
            },
            margin: { l: 32, r: 32, t: 40, b: 32 },
          }
        : {
            xaxis: {
              title: { text: 'x (m)' },
              gridcolor: plotColour('rule'),
              zerolinecolor: plotColour('rule'),
            },
            yaxis: {
              title: { text: `${projection === 'xy' ? 'y' : 'z'} (m)` },
              gridcolor: plotColour('rule'),
              zerolinecolor: plotColour('rule'),
              scaleanchor: 'x',
            },
          },
    [projection],
  );
  return (
    <Plot
      data={data}
      label={`${projection.toUpperCase()} trajectory and anchors`}
      layout={layout}
    />
  );
}

function Radar({ runId, time }: { runId: string; time: number }) {
  const [frameTime, setFrameTime] = useState(time);
  useEffect(() => {
    const timer = window.setTimeout(() => setFrameTime(time), 150);
    return () => window.clearTimeout(timer);
  }, [time]);
  const radar = useResource<RadarFrame>(
    `/api/runs/${encodeURIComponent(runId)}/radar-frame?time=${frameTime}`,
  );
  return (
    <section className="plot-panel">
      <div className="section-heading">
        <h3>Radar scan</h3>
        <span className="muted">Nearest recorded frame</span>
      </div>
      <LoadState resource={radar} label="Loading radar frame">
        {(frame) =>
          frame.points.length ? (
            <>
              <Plot
                label="Radar detections and associated centroid"
                data={[
                  {
                    type: 'scatter3d',
                    mode: 'markers',
                    name: 'Detections',
                    x: frame.points.map((p) => p[0]),
                    y: frame.points.map((p) => p[1]),
                    z: frame.points.map((p) => p[2]),
                    marker: { color: plotColour('reference'), size: 2 },
                  },
                  ...(frame.centroid
                    ? [
                        {
                          type: 'scatter3d' as const,
                          mode: 'markers' as const,
                          name: 'Centroid',
                          x: [frame.centroid[0]],
                          y: [frame.centroid[1]],
                          z: [frame.centroid[2]],
                          marker: { color: plotColour('estimate'), size: 7 },
                        },
                      ]
                    : []),
                ]}
                layout={{
                  scene: {
                    xaxis: { title: { text: 'x (m)' } },
                    yaxis: { title: { text: 'y (m)' } },
                    zaxis: { title: { text: 'z (m)' } },
                    aspectmode: 'data',
                    camera: { eye: { x: 1.8, y: 1.8, z: 1.3 } },
                  },
                  margin: { l: 32, r: 32, t: 40, b: 32 },
                }}
              />
              <p className="caption">
                {frame.sensor_id}, {frame.frame} frame, {formatNumber(frame.time)} s. Returns are
                not independent tag measurements.
              </p>
            </>
          ) : (
            <Empty title="No radar detections">
              <p>No scan is available at this cursor position.</p>
            </Empty>
          )
        }
      </LoadState>
    </section>
  );
}

function ReplayPlots({
  trajectory,
  diagnostics,
  run,
  time,
  setTime,
  modality,
  setModality,
  acceptance,
  setAcceptance,
}: {
  trajectory: Trajectory;
  diagnostics: Diagnostics;
  run: Run;
  time: number;
  setTime: (value: number) => void;
  modality: string;
  setModality: (value: string) => void;
  acceptance: string;
  setAcceptance: (value: string) => void;
}) {
  const [projection, setProjection] = useState<'xy' | 'xz'>('xy');
  const cursor = trajectory.times.reduce(
    (nearest, value, index) =>
      Math.abs(value - time) < Math.abs(trajectory.times[nearest] - time) ? index : nearest,
    0,
  );
  const actualTime = trajectory.times[cursor] ?? 0;
  const innovations = diagnostics.innovations.filter(
    (item) =>
      (modality === 'all' || item.modality === modality) &&
      (acceptance === 'all' || item.accepted === (acceptance === 'accepted')),
  );
  const errors = useMemo(() => positionErrors(trajectory), [trajectory]);
  const cursorLine = [
    {
      type: 'line' as const,
      x0: actualTime,
      x1: actualTime,
      y0: 0,
      y1: 1,
      yref: 'paper' as const,
      line: { color: plotColour('reference'), width: 1 },
    },
  ];
  const axisTime = {
    title: { text: 'Acquisition time (s)' },
    gridcolor: plotColour('rule'),
    zerolinecolor: plotColour('rule'),
  };
  const uncertaintySample = trajectory.covariance_diagonal[cursor];
  const uncertainty = finiteVector(uncertaintySample) ? uncertaintySample : null;
  const positionSample = trajectory.positions[cursor];
  const pos = finiteVector(positionSample) ? positionSample : null;
  return (
    <>
      <div className="timeline">
        <label htmlFor="replay-time">
          Shared time cursor <output htmlFor="replay-time">{formatNumber(actualTime, 2)} s</output>
        </label>
        <input
          id="replay-time"
          name="time"
          aria-label="Shared time cursor"
          type="range"
          min={trajectory.times[0] ?? 0}
          max={trajectory.times.at(-1) ?? 0}
          step="0.05"
          value={Math.max(trajectory.times[0] ?? 0, Math.min(time, trajectory.times.at(-1) ?? 0))}
          onChange={(event) => setTime(event.target.valueAsNumber)}
        />
        <span className="muted">Click a time plot to inspect its sample.</span>
      </div>
      <div className="replay-spatial">
        <section className="plot-panel primary-plot">
          <div className="section-heading">
            <h3>3D trajectory</h3>
            <span className="muted">World frame / metres</span>
          </div>
          <SpatialPlot trajectory={trajectory} cursor={cursor} projection="3d" />
        </section>
        <section className="plot-panel">
          <div className="section-heading">
            <h3>Planar projection</h3>
            <fieldset className="segmented" aria-label="Projection">
              <button
                type="button"
                aria-pressed={projection === 'xy'}
                onClick={() => setProjection('xy')}
              >
                XY
              </button>
              <button
                type="button"
                aria-pressed={projection === 'xz'}
                onClick={() => setProjection('xz')}
              >
                XZ
              </button>
            </fieldset>
          </div>
          <SpatialPlot trajectory={trajectory} cursor={cursor} projection={projection} />
        </section>
      </div>
      <MetricStrip run={run} />
      <section className="plot-panel error-plot">
        <div className="section-heading">
          <h3>Position error</h3>
          <span className="muted">Evaluation reference only</span>
        </div>
        <Plot
          data={[
            {
              type: 'scatter',
              mode: 'lines',
              name: '3D position error',
              x: trajectory.times,
              y: errors,
              line: { color: plotColour('estimate'), width: 2 },
              connectgaps: false,
            },
          ]}
          label="Position error against evaluation reference over time"
          onTime={setTime}
          layout={{
            xaxis: axisTime,
            yaxis: {
              title: { text: 'Position error (m)' },
              gridcolor: plotColour('rule'),
              rangemode: 'tozero',
            },
            shapes: cursorLine,
            showlegend: false,
          }}
        />
      </section>
      <div className="diagnostic-heading">
        <div>
          <h2>Measurement diagnostics</h2>
          <p className="muted">Pre-gating innovations include rejected observations.</p>
        </div>
        <div className="fields two">
          <label className="field">
            Modality
            <select
              name="modality"
              value={modality}
              onChange={(event) => setModality(event.target.value)}
            >
              <option value="all">All modalities</option>
              {['uwb', 'ftm', 'imu', 'radar'].map((value) => (
                <option key={value} value={value}>
                  {value.toUpperCase()}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Update outcome
            <select
              name="acceptance"
              value={acceptance}
              onChange={(event) => setAcceptance(event.target.value)}
            >
              <option value="all">All updates</option>
              <option value="accepted">Accepted only</option>
              <option value="rejected">Rejected only</option>
            </select>
          </label>
        </div>
      </div>
      <div className="diagnostic-grid">
        <section className="plot-panel">
          <h3>Normalised innovation squared</h3>
          {innovations.length ? (
            <Plot
              label="Accepted and rejected pre-gating NIS"
              onTime={setTime}
              data={[true, false].map((accepted) => ({
                type: 'scatter',
                mode: 'markers',
                name: accepted ? 'Accepted' : 'Rejected',
                x: innovations.filter((item) => item.accepted === accepted).map((item) => item.t),
                y: innovations.filter((item) => item.accepted === accepted).map((item) => item.nis),
                marker: {
                  color: plotColour(accepted ? 'estimate' : 'error'),
                  size: 5,
                  symbol: accepted ? 'circle' : 'x',
                },
              }))}
              layout={{
                xaxis: axisTime,
                yaxis: {
                  title: { text: 'NIS (dimensionless)' },
                  gridcolor: plotColour('rule'),
                  rangemode: 'tozero',
                },
                shapes: cursorLine,
              }}
            />
          ) : (
            <Empty title="No matching updates">
              <p>Choose all modalities or all outcomes to inspect retained innovations.</p>
            </Empty>
          )}
          <p className="caption">
            NIS is dimensionless. Observation dimensions and gates are recorded in diagnostic
            metadata.
          </p>
        </section>
        <section className="plot-panel">
          <h3>Residual acceleration bias</h3>
          {trajectory.biases.some(finiteVector) ? (
            <Plot
              label="Estimated residual IMU bias components"
              onTime={setTime}
              data={[0, 1, 2].map((axis) => ({
                type: 'scatter',
                mode: 'lines',
                name: `${'xyz'[axis]} bias`,
                x: trajectory.times,
                y: coordinates(trajectory.biases, axis),
                line: {
                  color: plotColour(axis === 0 ? 'estimate' : axis === 1 ? 'reference' : 'muted'),
                  dash: axis === 2 ? 'dash' : 'solid',
                },
                connectgaps: false,
              }))}
              layout={{
                xaxis: axisTime,
                yaxis: { title: { text: 'Residual bias (m/s²)' }, gridcolor: plotColour('rule') },
                shapes: cursorLine,
              }}
            />
          ) : (
            <Empty title="No inertial bias state">
              <p>This estimator uses constant-velocity propagation or WLS.</p>
            </Empty>
          )}
        </section>
      </div>
      <div className="diagnostic-grid">
        <Radar runId={run.run_id} time={actualTime} />
        <section className="cursor-state">
          <h3>State at {formatNumber(actualTime, 2)} s</h3>
          <table>
            <thead>
              <tr>
                <th>Axis</th>
                <th>Position (m)</th>
                <th>Marginal σ (m)</th>
              </tr>
            </thead>
            <tbody>
              {[0, 1, 2].map((axis) => (
                <tr key={axis}>
                  <th scope="row">{'xyz'[axis]}</th>
                  <td>{formatNumber(pos?.[axis])}</td>
                  <td>
                    {formatNumber(
                      uncertainty && uncertainty[axis] >= 0 ? Math.sqrt(uncertainty[axis]) : null,
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="caption">
            The trajectory shows engine-supplied nominal confidence geometry. The 3D ellipsoid uses
            the full position covariance; XY and XZ show separate marginal ellipses. Nominal
            probability is not measured coverage.
          </p>
          <details>
            <summary>Diagnostic metadata</summary>
            <RecordTable record={diagnostics.metadata} />
          </details>
        </section>
      </div>
      <details className="innovation-records">
        <summary>Update ledger ({innovations.length} shown)</summary>
        <p className="caption">
          First 100 updates matching the current filters. The numerical export preserves the full
          ledger.
        </p>
        <TableScroll label="Measurement update ledger">
          <table>
            <thead>
              <tr>
                <th>Time (s)</th>
                <th>Sensor</th>
                <th>Residual (m)</th>
                <th>NIS</th>
                <th>Outcome</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {innovations.slice(0, 100).map((item) => (
                <tr key={`${item.t}-${item.modality}-${item.sensor_id}`}>
                  <td>{formatNumber(item.t)}</td>
                  <td>
                    {item.modality} / {item.sensor_id}
                  </td>
                  <td>
                    {Array.isArray(item.residual)
                      ? item.residual.map((value) => formatNumber(value)).join(', ')
                      : formatNumber(item.residual)}
                  </td>
                  <td>{formatNumber(item.nis)}</td>
                  <td>{item.accepted ? 'Accepted' : 'Rejected'}</td>
                  <td>{item.reason || 'None recorded'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      </details>
      <p className="caption">
        {trajectory.decimated
          ? `Viewer decimated to ${trajectory.times.length} of ${trajectory.total_points} points. Download numerical artefacts for full resolution.`
          : `Showing ${trajectory.times.length} output samples.`}
      </p>
    </>
  );
}

export default function Replay(props: {
  run: Run;
  time: number;
  setTime: (value: number) => void;
  modality: string;
  setModality: (value: string) => void;
  acceptance: string;
  setAcceptance: (value: string) => void;
  revision: number;
}) {
  const ready = props.run.status === 'completed';
  const trajectory = useResource<Trajectory>(
    ready ? `/api/runs/${encodeURIComponent(props.run.run_id)}/trajectory?max_points=2000` : null,
    props.revision,
  );
  const diagnostics = useResource<Diagnostics>(
    ready ? `/api/runs/${encodeURIComponent(props.run.run_id)}/diagnostics?max_points=2000` : null,
    props.revision,
  );
  if (!ready)
    return (
      <Empty title={`Run ${props.run.status}`}>
        <p>
          {props.run.error ||
            props.run.message ||
            'Replay becomes available after a completed run. Partial and failed records remain preserved.'}
        </p>
      </Empty>
    );
  return (
    <LoadState resource={trajectory} label="Loading trajectory">
      {(trajectoryData) => (
        <LoadState resource={diagnostics} label="Loading diagnostics">
          {(diagnosticData) =>
            trajectoryData.times.length ? (
              <ReplayPlots {...props} trajectory={trajectoryData} diagnostics={diagnosticData} />
            ) : (
              <Empty title="No trajectory samples">
                <p>
                  This result contains no supported trajectory samples. Inspect its run provenance
                  and numerical artefacts.
                </p>
              </Empty>
            )
          }
        </LoadState>
      )}
    </LoadState>
  );
}
