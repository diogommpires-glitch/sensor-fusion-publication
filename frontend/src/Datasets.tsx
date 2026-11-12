import { RecordTable, Status } from './components';
import type { Catalog } from './types';

export default function Datasets({ catalog, onBuild }: { catalog: Catalog; onBuild: () => void }) {
  return (
    <div className="dataset-view">
      <div className="section-heading">
        <div>
          <p className="intro">
            Access, frame certification and development-only calibration precede scientific claims.
          </p>
        </div>
        <button type="button" onClick={onBuild}>
          Build experiment
        </button>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Dataset</th>
              <th>Readiness</th>
              <th>Evidence and limitations</th>
            </tr>
          </thead>
          <tbody>
            {catalog.datasets.map((dataset) => (
              <tr key={dataset.id}>
                <th scope="row">
                  {dataset.name}
                  <small>{dataset.id}</small>
                </th>
                <td>
                  <Status value={dataset.status} />
                </td>
                <td>
                  {dataset.reason ||
                    (dataset.id === 'fixture'
                      ? 'Generated observations and known state for mathematical validation only.'
                      : 'Inspect the registered source and calibration provenance before use.')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="dataset-columns">
        <section>
          <h2>Registered sessions</h2>
          {catalog.datasets.map((dataset) => (
            <details key={dataset.id} open={dataset.id === 'fixture'}>
              <summary>{dataset.name}</summary>
              <p className="table-scroll-hint" id={`${dataset.id}-session-scroll-hint`}>
                Scroll sideways to see all session columns.
              </p>
              <section
                className="table-scroll"
                aria-label={`${dataset.name} sessions`}
                aria-describedby={`${dataset.id}-session-scroll-hint`}
                // biome-ignore lint/a11y/noNoninteractiveTabindex: Keyboard users need focus here to scroll every session column.
                tabIndex={0}
              >
                <table>
                  <thead>
                    <tr>
                      <th>Session</th>
                      <th>Partition</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dataset.sessions.map((session) => (
                      <tr key={session.id}>
                        <td>{session.label}</td>
                        <td>{session.partition}</td>
                        <td>{session.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            </details>
          ))}
        </section>
        <section>
          <h2>Calibration registry</h2>
          {catalog.calibrations.length ? (
            catalog.calibrations.map((calibration, index) => {
              const identifier = [calibration.calibration_id, calibration.id].find(
                (value): value is string => typeof value === 'string' && value.trim() !== '',
              );
              let title = identifier ?? 'Unidentified calibration record';
              if (typeof calibration.name === 'string' && calibration.name.trim()) {
                title = calibration.name;
              }
              return (
                <details key={identifier ?? `unidentified-${index}`}>
                  <summary>{title}</summary>
                  {!identifier && (
                    <p className="error" role="status">
                      This record has no calibration identifier. Inspect the saved metadata before
                      use.
                    </p>
                  )}
                  <RecordTable record={calibration} />
                </details>
              );
            })
          ) : (
            <p>
              No certified real-data calibration is registered. The synthetic fixture uses its
              declared generating geometry.
            </p>
          )}
          <h3 className="spaced">Source-access gate</h3>
          <p>
            Source checksums, actual payload schemas, units, clock relationships and frames must be
            certified before real-data integration.
          </p>
          <p>
            RoboLoc-G: still, zigzag1 and zigzag2 are development; zigzag3, zigzag4 and random1 are
            validation; circle and random2 are protected final.
          </p>
        </section>
      </div>
      {catalog.limitations.length > 0 && (
        <section className="limitations">
          <h2>Current limitations</h2>
          <ul>
            {catalog.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
