import { type ReactNode, useId } from 'react';
import type { Resource } from './api';
import type { Run, RunConfig } from './types';

export function methodLabel(config: RunConfig) {
  const factors = config.factors;
  if (!factors) return config.method;
  return `${factors.propagation === 'cv' ? 'CV' : 'Inertial'} / radar ${factors.radar ? 'on' : 'off'} / gate ${factors.gating ? 'on' : 'off'} (exploratory)`;
}

export function Status({ value }: { value: string }) {
  return <span className={`status status-${value}`}>{value.replaceAll('_', ' ')}</span>;
}

export function TableScroll({ label, children }: { label: string; children: ReactNode }) {
  const hint = useId();
  return (
    <>
      <p className="table-scroll-hint" id={hint}>
        Scroll sideways to see all columns.
      </p>
      <section
        className="table-scroll"
        aria-label={label}
        aria-describedby={hint}
        // biome-ignore lint/a11y/noNoninteractiveTabindex: Keyboard users need focus here to scroll every table column.
        tabIndex={0}
      >
        {children}
      </section>
    </>
  );
}

export function Empty({
  title,
  children,
  level = 3,
}: {
  title: string;
  children: ReactNode;
  level?: 2 | 3;
}) {
  const Heading = level === 2 ? 'h2' : 'h3';
  return (
    <div className="empty">
      <Heading>{title}</Heading>
      <div>{children}</div>
    </div>
  );
}

export function LoadState<T>({
  resource,
  children,
  label = 'Loading local records',
}: {
  resource: Resource<T>;
  children: (data: T) => ReactNode;
  label?: string;
}) {
  if (resource.error)
    return (
      <div className="error" role="alert">
        <strong>Could not load this region.</strong>
        <p>{resource.error}</p>
        <p>Check the local engine, then use Refresh records.</p>
      </div>
    );
  if (!resource.data)
    return (
      <div className="skeleton" role="status" aria-label={label}>
        <span />
        <span />
        <span />
        <p>{label}…</p>
      </div>
    );
  return children(resource.data);
}

export function formatNumber(value: unknown, digits = 3) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'Unavailable';
  return value.toLocaleString('en-GB', { maximumFractionDigits: digits });
}

export function MetricStrip({ run }: { run: Run }) {
  const rows = [
    ['Position RMSE', 'position_rmse_m', 'm'],
    ['95th percentile error', 'p95_error_m', 'm'],
    ['Availability', 'availability', 'fraction'],
    ['Empirical 95% position coverage', 'position_coverage_95', 'fraction'],
  ];
  return (
    <dl className="metric-strip">
      {rows.map(([name, key, unit]) => (
        <div key={key}>
          <dt>{name}</dt>
          <dd>
            {formatNumber(run.metrics?.[key])} <small>{unit}</small>
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function RunSelect({
  runs,
  selected,
  onChange,
  label = 'Selected run',
}: {
  runs: Run[];
  selected: string;
  onChange: (id: string) => void;
  label?: string;
}) {
  return (
    <label className="field run-select">
      {label}
      <select
        aria-label={label}
        value={selected}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Choose a run</option>
        {runs.map((run) => (
          <option key={run.run_id} value={run.run_id}>
            {methodLabel(run.config)} / {run.config.session_id} / {run.run_id.slice(0, 8)} /{' '}
            {run.status}
          </option>
        ))}
      </select>
    </label>
  );
}

export function RecordTable({ record }: { record: Record<string, unknown> }) {
  return (
    <dl className="record">
      {Object.entries(record).map(([key, value]) => (
        <div key={key}>
          <dt>{key.replaceAll('_', ' ')}</dt>
          <dd>
            {typeof value === 'object' ? (
              <pre>{JSON.stringify(value, null, 2)}</pre>
            ) : (
              String(value ?? 'Unavailable')
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
