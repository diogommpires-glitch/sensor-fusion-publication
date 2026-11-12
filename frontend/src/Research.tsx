import type { Data } from 'plotly.js';
import { useMemo } from 'react';
import { useResource } from './api';
import { Empty, formatNumber, LoadState, RecordTable, Status, TableScroll } from './components';
import Plot, { plotColour } from './Plot';
import type { HardwareReplay, Investigation, ResearchCatalog } from './types';

function InvestigationRecord({
  record,
  onReplay,
}: {
  record: Investigation;
  onReplay: (id: string) => void;
}) {
  return (
    <section className="research-investigation">
      <h3>
        {record.id} <Status value={record.status} />
      </h3>
      <p className="provenance-line">
        Exploratory investigation on previously inspected recordings. These are not new held-out
        results.
      </p>
      <details>
        <summary>Protocol identity and fixed conditions</summary>
        <p>
          Protocol hash: <code>{record.protocol_hash}</code>
        </p>
        <RecordTable record={record.protocol} />
      </details>
      {record.results && (
        <>
          <p>{record.results.macro_scope}</p>
          <TableScroll label="Registered factorial contrasts">
            <table>
              <caption>Registered factorial contrasts</caption>
              <thead>
                <tr>
                  <th>Factors</th>
                  <th>Order</th>
                  <th>Contrast in position RMSE (m)</th>
                  <th>Complete cells</th>
                </tr>
              </thead>
              <tbody>
                {record.results.macro_contrasts.map((effect) => (
                  <tr key={effect.factors}>
                    <th scope="row">{effect.factors}</th>
                    <td>{effect.order}</td>
                    <td>{formatNumber(effect.contrast_rmse_m)}</td>
                    <td>{effect.complete_cells ? 'Yes' : 'No; unavailable contrast'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          <details>
            <summary>Supported and unresolved interpretation</summary>
            <RecordTable record={record.results.interpretation} />
          </details>
        </>
      )}
      {record.runs.length ? (
        <details>
          <summary>Condition runs ({record.runs.length})</summary>
          <TableScroll label="Exploratory condition runs">
            <table>
              <thead>
                <tr>
                  <th>Session</th>
                  <th>Condition</th>
                  <th>Status</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {record.runs.map((run) => (
                  <tr key={run.key}>
                    <th scope="row">{run.session}</th>
                    <td>{run.condition}</td>
                    <td>
                      <Status value={run.status} />
                    </td>
                    <td>
                      <button type="button" onClick={() => onReplay(run.run_id)}>
                        Replay {run.condition}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        </details>
      ) : (
        <p>No condition has executed yet.</p>
      )}
    </section>
  );
}

function RangeReplay({ record }: { record: HardwareReplay }) {
  const traces = useMemo<Data[]>(
    () => [
      {
        type: 'scatter',
        mode: 'markers',
        name: 'Received range; freshness may be unknown',
        x: record.observations.map((row) => row.time_s),
        y: record.observations.map((row) => row.distance_m),
        marker: { color: plotColour('muted'), size: 5 },
        connectgaps: false,
      },
      {
        type: 'scatter',
        mode: 'markers',
        name: 'Verified new observations',
        x: record.observations.map((row) => row.time_s),
        y: record.observations.map((row) => (row.new_observation ? row.distance_m : null)),
        marker: { color: plotColour('estimate'), size: 7 },
        connectgaps: false,
      },
    ],
    [record],
  );
  const age = useMemo<Data[]>(
    () => [
      {
        type: 'scatter',
        mode: 'markers',
        name: 'Reported device age',
        x: record.observations.map((row) => row.time_s),
        y: record.observations.map((row) => row.age_dist_ms),
        marker: { color: plotColour('reference'), size: 5 },
        connectgaps: false,
      },
    ],
    [record],
  );
  return (
    <>
      <h3>Range-only replay: {record.metadata.record_id}</h3>
      {record.metadata.synthetic && (
        <p className="notice">
          Synthetic hardware fixture. No physical measurement or accuracy claim.
        </p>
      )}
      <p>
        {record.scope} {record.metadata.scope}
      </p>
      <p className="provenance-line">
        Host receive time and receive-minus-age proxies are not verified acquisition clocks.
        Repeated packets are not automatically independent observations.
      </p>
      {!record.metadata.reference_ready && (
        <p className="notice">
          Reference evidence is incomplete. This recording cannot establish range accuracy.
        </p>
      )}
      <div className="diagnostic-grid">
        <section className="plot-panel">
          <h4>Processed radar range</h4>
          <Plot
            label="One-dimensional processed radar range and verified freshness"
            data={traces}
            layout={{
              xaxis: { title: { text: 'Host receive elapsed time (s)' } },
              yaxis: { title: { text: 'Range (m)' } },
            }}
          />
        </section>
        <section className="plot-panel">
          <h4>Device-reported age</h4>
          <Plot
            label="Device-reported range age in milliseconds"
            data={age}
            layout={{
              xaxis: { title: { text: 'Host receive elapsed time (s)' } },
              yaxis: { title: { text: 'Reported age (ms)' } },
            }}
          />
        </section>
      </div>
      <div className="record-columns">
        <section>
          <h4>Measured reference and declared uncertainty</h4>
          <RecordTable record={record.metadata.reference} />
        </section>
        <section>
          <h4>Capability and firmware evidence</h4>
          <RecordTable record={record.metadata.capability} />
        </section>
      </div>
      <details>
        <summary>Freshness and clock ledger</summary>
        <TableScroll label="Freshness and clock ledger">
          <table>
            <thead>
              <tr>
                <th>Host elapsed (s)</th>
                <th>Range (m)</th>
                <th>New observation</th>
                <th>Reason</th>
                <th>Clock basis</th>
              </tr>
            </thead>
            <tbody>
              {record.observations.map((row, index) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: Immutable JSONL source-row identity preserves duplicate receipts with equal timestamps.
                <tr key={`${row.time_s}-${index}`}>
                  <td>{formatNumber(row.time_s)}</td>
                  <td>{formatNumber(row.distance_m)}</td>
                  <td>{row.new_observation ? 'Yes' : 'No'}</td>
                  <td>{row.reason}</td>
                  <td>{row.clock_basis}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      </details>
    </>
  );
}

export default function Research({
  revision,
  recordId,
  setRecord,
  onReplay,
}: {
  revision: number;
  recordId: string;
  setRecord: (id: string) => void;
  onReplay: (id: string) => void;
}) {
  const research = useResource<ResearchCatalog>('/api/research', revision);
  const trial = useResource<HardwareReplay>(
    recordId ? `/api/hardware/trials/${encodeURIComponent(recordId)}` : null,
    revision,
  );
  return (
    <div className="research-view">
      <h2>Research evidence and hardware readiness</h2>
      <p className="intro">
        Keep historical results, exploratory explanations and future physical measurements distinct.
        Completing software does not complete a physical experiment.
      </p>
      <LoadState resource={research} label="Loading research and hardware registers">
        {(catalog) => (
          <>
            <div className="study-columns">
              <section>
                <h3>Historical v1 is preserved</h3>
                <p>
                  The original source executes through its separately pinned interpreter. Current
                  exploratory conditions never replace the original study.
                </p>
                <details>
                  <summary>Historical wheel and source fingerprints</summary>
                  <RecordTable record={catalog.historical_v1} />
                </details>
                <pre>.venv/Scripts/python.exe -m sensorfusion history catalog</pre>
              </section>
              <section>
                <h3>Physical recording gate</h3>
                <Status
                  value={catalog.hardware.physical_inventory_verified ? 'verified' : 'blocked'}
                />
                <ul>
                  {catalog.hardware.blocking_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
                <p>
                  {catalog.hardware.scope} Documented device support does not verify the available
                  model, firmware or freshness semantics.
                </p>
                <p>
                  Planned conditions: {catalog.hardware.schedule.length}. Recorded trials:{' '}
                  {catalog.hardware.trials.length}. Sensor channels remain independent.
                </p>
              </section>
            </div>
            <section>
              <h3>Exploratory factorial investigations</h3>
              {catalog.investigations.length ? (
                catalog.investigations.map((record) => (
                  <InvestigationRecord key={record.id} record={record} onReplay={onReplay} />
                ))
              ) : (
                <Empty title="No registered mechanism investigation">
                  <p>
                    No exploratory factorial result has been registered. The negative v1 result
                    remains unchanged.
                  </p>
                </Empty>
              )}
            </section>
            <section className="spaced">
              <h3>Hardware protocol and recording</h3>
              <p>
                H1 calibrates; H2 selects the declared grid; H3 remains protected until freeze. Each
                trial has ten settling seconds and thirty evaluation seconds.
              </p>
              <details>
                <summary>Registered physical trial schedule</summary>
                <TableScroll label="Registered physical trial schedule">
                  <table>
                    <thead>
                      <tr>
                        <th>Trial</th>
                        <th>Session</th>
                        <th>Condition</th>
                        <th>Nominal distance (m)</th>
                        <th>Duration (s)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {catalog.hardware.schedule.map((row) => (
                        <tr key={row.trial_id}>
                          <th scope="row">{row.trial_id}</th>
                          <td>{row.session}</td>
                          <td>{row.condition}</td>
                          <td>{formatNumber(row.nominal_distance_m)}</td>
                          <td>{row.duration_s}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableScroll>
              </details>
              <details>
                <summary>Operator commands after equipment becomes accessible</summary>
                <p>
                  Verify hardware and freshness first. Supply measured reference evidence and
                  consent. Replace the capability, trial, port and reference placeholders with
                  registered values.
                </p>
                <pre>
                  {
                    '.venv/Scripts/python.exe -m sensorfusion hardware catalog\n.venv/Scripts/python.exe -m sensorfusion hardware schedule\n.venv/Scripts/python.exe -m sensorfusion hardware capability --config capability.json\n.venv/Scripts/python.exe -m sensorfusion hardware reference --trial TRIAL_ID\n.venv/Scripts/python.exe -m sensorfusion hardware record --trial TRIAL_ID --capability CAPABILITY_ID --reference measured-reference.json --port PORT'
                  }
                </pre>
              </details>
              {catalog.hardware.capabilities.length > 0 && (
                <details>
                  <summary>Registered capability declarations</summary>
                  {catalog.hardware.capabilities.map((value, index) => (
                    <RecordTable key={String(value.capability_id ?? index)} record={value} />
                  ))}
                </details>
              )}
            </section>
            <section className="spaced">
              <h3>One-dimensional hardware replay</h3>
              <label className="field">
                Hardware recording
                <select value={recordId} onChange={(event) => setRecord(event.target.value)}>
                  <option value="">Choose a completed recording</option>
                  {catalog.hardware.trials.map((record) => (
                    <option
                      key={record.record_id}
                      value={record.record_id}
                      disabled={record.status !== 'completed'}
                    >
                      {record.record_id} / {record.status}
                    </option>
                  ))}
                </select>
              </label>
              {recordId ? (
                <LoadState resource={trial} label="Loading range recording">
                  {(record) => <RangeReplay record={record} />}
                </LoadState>
              ) : (
                <Empty title="No range recording selected">
                  <p>
                    No physical measurements are substituted by simulation. Completed recordings
                    will show ranges, freshness and their reference evidence here.
                  </p>
                </Empty>
              )}
            </section>
          </>
        )}
      </LoadState>
    </div>
  );
}
