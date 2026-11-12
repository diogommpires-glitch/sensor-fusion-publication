import { useCallback, useEffect, useRef, useState } from 'react';
import { request, useResource } from './api';
import Comparison from './Comparison';
import { Empty, LoadState, methodLabel, RecordTable, RunSelect, Status } from './components';
import Datasets from './Datasets';
import Experiment from './Experiment';
import Replay from './Replay';
import Research from './Research';
import Study from './Study';
import { type Catalog, isActive, type Run } from './types';

const tabs = [
  ['datasets', 'Datasets & calibration'],
  ['experiment', 'Experiment builder'],
  ['replay', 'Replay & diagnostics'],
  ['comparison', 'Run comparison'],
  ['study', 'Frozen study & methods'],
  ['research', 'Research & hardware'],
] as const;

function RunRecord({
  run,
  onCancel,
  cancelling,
}: {
  run: Run;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const artifacts = useResource<{ artifacts: { id: string; name: string; media_type: string }[] }>(
    run.status === 'completed' ? `/api/runs/${encodeURIComponent(run.run_id)}/artifacts` : null,
  );
  const active = isActive(run.status);
  return (
    <section className="run-record">
      <div className="run-record-heading">
        <div>
          <h3>
            {methodLabel(run.config)} / {run.config.session_id} <Status value={run.status} />
          </h3>
          <p>{run.run_id}</p>
        </div>
        {active && (
          <button type="button" disabled={cancelling} aria-busy={cancelling} onClick={onCancel}>
            {cancelling ? 'Cancelling…' : 'Cancel run'}
          </button>
        )}
      </div>
      {run.config.dataset_id === 'fixture' && (
        <p className="provenance-line">Synthetic validation fixture, not a real-data study</p>
      )}
      {run.config.factors && (
        <p className="provenance-line">
          Exploratory factor condition on previously inspected recordings. This is not an untouched
          final result.
        </p>
      )}
      {active && (
        <div className="progress">
          <progress aria-label="Experiment progress" value={run.progress ?? 0} max="1" />
          <span role="status">{run.message || run.status}</span>
        </div>
      )}
      {run.error && (
        <p className="error" role="alert">
          {run.error}
        </p>
      )}
      <details>
        <summary>Saved configuration, provenance and exports</summary>
        <div className="record-columns">
          <section>
            <h4>Resolved configuration</h4>
            <RecordTable record={run.config as unknown as Record<string, unknown>} />
          </section>
          <section>
            <h4>Provenance</h4>
            {run.provenance ? (
              <RecordTable record={run.provenance} />
            ) : (
              <p>No provenance is available yet.</p>
            )}
            <h4>Download artefacts</h4>
            {run.status !== 'completed' ? (
              <p>Exports become available after completed finalisation.</p>
            ) : (
              <LoadState resource={artifacts} label="Loading export register">
                {(data) =>
                  data.artifacts.length ? (
                    <ul className="artifact-list">
                      {data.artifacts.map((artifact) => (
                        <li key={artifact.id}>
                          <a
                            href={`/api/runs/${encodeURIComponent(run.run_id)}/artifacts/${encodeURIComponent(artifact.id)}`}
                            download
                          >
                            {artifact.name}
                          </a>
                          <small>{artifact.media_type}</small>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>Artefacts become available when the engine saves them.</p>
                  )
                }
              </LoadState>
            )}
          </section>
        </div>
      </details>
    </section>
  );
}

export default function App() {
  const navigation = useRef<HTMLElement>(null);
  const [params, setParams] = useState(() => new URLSearchParams(window.location.search));
  const [revision, setRevision] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const catalog = useResource<Catalog>('/api/catalog', revision);
  const runList = useResource<{ runs: Run[] }>('/api/runs', revision);
  const health = useResource<{ version: string }>('/api/health', revision);
  const selectedId = params.get('run') ?? '';
  const selected = useResource<Run>(
    selectedId ? `/api/runs/${encodeURIComponent(selectedId)}` : null,
    revision,
  );
  const tab = tabs.some(([key]) => key === params.get('tab')) ? params.get('tab') : 'experiment';
  useEffect(() => {
    const nav = navigation.current;
    if (!nav) return;
    const revealActiveTab = () => {
      const activeTab = nav.querySelector(`a[href="?tab=${tab}"]`);
      if (!activeTab) return;
      const bounds = nav.getBoundingClientRect();
      const activeBounds = activeTab.getBoundingClientRect();
      if (activeBounds.left < bounds.left) nav.scrollLeft += activeBounds.left - bounds.left;
      else if (activeBounds.right > bounds.right)
        nav.scrollLeft += activeBounds.right - bounds.right;
    };
    revealActiveTab();
    const observer = new ResizeObserver(revealActiveTab);
    observer.observe(nav);
    return () => observer.disconnect();
  }, [tab]);
  const runs = runList.data?.runs ?? [];
  const active =
    runs.some((run) => isActive(run.status)) || (selected.data && isActive(selected.data.status));
  const update = useCallback((values: Record<string, string>, replace = false) => {
    const next = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    const url = `${window.location.pathname}?${next}`;
    if (replace) window.history.replaceState(null, '', url);
    else window.history.pushState(null, '', url);
    setParams(next);
  }, []);
  useEffect(() => {
    const changed = () => setParams(new URLSearchParams(window.location.search));
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setRevision((value) => value + 1), 1200);
    return () => window.clearInterval(timer);
  }, [active]);
  const setTime = useCallback(
    (time: number) => update({ time: String(Math.max(0, time)) }, true),
    [update],
  );
  async function cancel() {
    setCancelling(true);
    setActionError(null);
    try {
      await request(`/api/runs/${encodeURIComponent(selectedId)}/cancel`, { method: 'POST' });
      setRevision((value) => value + 1);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setCancelling(false);
    }
  }
  const openReplay = (id: string) => update({ tab: 'replay', run: id, time: '0' });
  const timeValue = Number(params.get('time') ?? 0);
  const time = Number.isFinite(timeValue) && timeValue >= 0 ? timeValue : 0;
  return (
    <div className="app-shell">
      <a className="skip-link" href="#workbench">
        Skip to workbench
      </a>
      <header className="app-header">
        <a className="wordmark" href="?tab=experiment">
          Sensor Fusion
        </a>
        <div className="engine-state">
          <span>Local scientific workbench</span>
          <Status value={health.data ? 'connected' : health.error ? 'offline' : 'connecting'} />
          {health.data && <small>v{health.data.version}</small>}
        </div>
      </header>
      <nav className="app-nav" aria-label="Workbench views" ref={navigation}>
        {tabs.map(([key, label]) => (
          <a
            key={key}
            href={`?tab=${key}`}
            aria-current={tab === key ? 'page' : undefined}
            onClick={(event) => {
              event.preventDefault();
              update({ tab: key });
            }}
          >
            {label}
          </a>
        ))}
      </nav>
      <main id="workbench">
        <div className="workspace-toolbar">
          <h1>
            {tab === 'datasets'
              ? 'Datasets and calibration'
              : tabs.find(([key]) => key === tab)?.[1]}
          </h1>
          <button
            type="button"
            className="refresh"
            onClick={() => setRevision((value) => value + 1)}
            aria-busy={catalog.loading || runList.loading}
          >
            Refresh records
          </button>
        </div>
        {actionError && (
          <p className="error" role="alert">
            {actionError}
          </p>
        )}
        <LoadState resource={catalog} label="Loading datasets and engine configuration">
          {(data) => (
            <>
              {tab === 'datasets' && (
                <Datasets catalog={data} onBuild={() => update({ tab: 'experiment' })} />
              )}
              {tab === 'experiment' && (
                <LoadState resource={runList} label="Loading run ledger">
                  {() => (
                    <Experiment
                      catalog={data}
                      runs={runs}
                      onRun={(id) => {
                        openReplay(id);
                        setRevision((value) => value + 1);
                      }}
                      onReplay={openReplay}
                    />
                  )}
                </LoadState>
              )}
              {tab === 'replay' && (
                <>
                  <div className="replay-selector">
                    <RunSelect
                      runs={runs}
                      selected={selectedId}
                      onChange={(id) => update({ run: id, time: '0' })}
                    />
                    <button
                      type="button"
                      onClick={() =>
                        update({
                          tab: 'comparison',
                          compare: runs.find((run) => run.run_id !== selectedId)?.run_id ?? '',
                        })
                      }
                    >
                      Compare this run
                    </button>
                  </div>
                  {selectedId ? (
                    <LoadState resource={selected} label="Loading selected run">
                      {(run) => (
                        <>
                          <RunRecord run={run} onCancel={cancel} cancelling={cancelling} />
                          <Replay
                            run={run}
                            time={time}
                            setTime={setTime}
                            modality={params.get('modality') ?? 'all'}
                            setModality={(value) => update({ modality: value }, true)}
                            acceptance={params.get('outcome') ?? 'all'}
                            setAcceptance={(value) => update({ outcome: value }, true)}
                            revision={revision}
                          />
                        </>
                      )}
                    </LoadState>
                  ) : (
                    <Empty title="Choose an experiment to replay">
                      <p>
                        Run an estimator, then inspect its trajectory, reference errors and
                        measurement diagnostics.
                      </p>
                      <button type="button" onClick={() => update({ tab: 'experiment' })}>
                        Build experiment
                      </button>
                    </Empty>
                  )}
                </>
              )}
              {tab === 'comparison' && (
                <Comparison
                  runs={runs}
                  leftId={selectedId}
                  rightId={params.get('compare') ?? ''}
                  setLeft={(id) => update({ run: id })}
                  setRight={(id) => update({ compare: id })}
                  revision={revision}
                />
              )}
              {tab === 'study' && <Study catalog={data} revision={revision} />}
              {tab === 'research' && (
                <Research
                  revision={revision}
                  recordId={params.get('trial') ?? ''}
                  setRecord={(id) => update({ trial: id })}
                  onReplay={openReplay}
                />
              )}
            </>
          )}
        </LoadState>
      </main>
      <footer>
        <span>Metres / seconds / radians</span>
        <span>Reference is evaluation-only. Synthetic validation is labelled.</span>
      </footer>
    </div>
  );
}
