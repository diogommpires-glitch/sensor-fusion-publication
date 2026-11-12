import { useState } from 'react';
import { useResource } from './api';
import { Empty, LoadState, RecordTable, Status } from './components';
import type { Catalog, Studies } from './types';

export default function Study({ catalog, revision }: { catalog: Catalog; revision: number }) {
  const studies = useResource<Studies>('/api/studies', revision);
  const [selected, setSelected] = useState('');
  const study = useResource<Record<string, unknown>>(
    selected ? `/api/studies/${encodeURIComponent(selected)}` : null,
    revision,
  );
  return (
    <div className="study">
      <p className="intro">
        A registered protocol is a plan. Completion requires real measurements, immutable provenance
        and preserved failures.
      </p>
      <LoadState resource={studies} label="Loading study register">
        {(data) => (
          <>
            {data.blockers.length > 0 && (
              <section className="notice">
                <h2>Study gate is incomplete</h2>
                <ul>
                  {data.blockers.map((blocker) => (
                    <li key={blocker}>{blocker}</li>
                  ))}
                </ul>
              </section>
            )}
            <div className="study-columns">
              <section>
                <h2>Primary question</h2>
                <p>
                  Does full fusion with innovation gating (M7) reduce macro-average final-trajectory
                  3D position RMSE relative to the UWB constant-velocity filter (M2)?
                </p>
                <p>
                  The controlled gantry study uses circle and random2 as protected final sessions. A
                  failed hypothesis is a valid result.
                </p>
                <h3 className="spaced">Evaluation limits</h3>
                <p>
                  Reference positions support position error and position-uncertainty coverage. They
                  do not establish full-state NEES.
                </p>
                <p>
                  MILUV tests the calibrated range subsystem with M1 and M2. It does not externally
                  validate Wi-Fi, inertial modelling or radar.
                </p>
              </section>
              <section>
                <h2>Frozen study register</h2>
                {data.studies.length ? (
                  <>
                    <label className="field">
                      Study
                      <select
                        name="study"
                        value={selected}
                        onChange={(event) => setSelected(event.target.value)}
                      >
                        <option value="">Choose a registered study</option>
                        {data.studies.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name ?? item.id} / {item.status ?? 'status unavailable'}
                          </option>
                        ))}
                      </select>
                    </label>
                    {selected && (
                      <LoadState resource={study}>
                        {(value) => <RecordTable record={value} />}
                      </LoadState>
                    )}
                  </>
                ) : (
                  <Empty title="No completed frozen study">
                    <p>
                      No real-data study result is registered. Exploratory fixture runs remain
                      separate from scientific completion.
                    </p>
                  </Empty>
                )}
                <details>
                  <summary>Registered protocol</summary>
                  <RecordTable record={data.protocol} />
                </details>
              </section>
            </div>
          </>
        )}
      </LoadState>
      <section className="method-matrix">
        <h2>Eight-method comparison</h2>
        <table>
          <thead>
            <tr>
              <th>Method</th>
              <th>Estimator</th>
              <th>Observation classes</th>
            </tr>
          </thead>
          <tbody>
            {catalog.methods.map((method) => (
              <tr key={method.id}>
                <th scope="row">{method.id}</th>
                <td>{method.name}</td>
                <td>{method.modalities.join(', ').toUpperCase()}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="caption">
          AOP is the primary radar. ISK0 and ISK1 are separate substitutions, not jointly fused
          independent observations.
        </p>
      </section>
      <section className="invariant-list">
        <h2>Scientific boundaries</h2>
        <div>
          <Status value="preserved" />
          <p>
            Reference isolation. Development-only calibration. Arrival-order processing. No
            automatic study reset. Failed trajectories retained.
          </p>
        </div>
      </section>
    </div>
  );
}
