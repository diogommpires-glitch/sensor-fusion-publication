import type { Data, Layout, PlotlyHTMLElement, PlotMouseEvent } from 'plotly.js';
import { useEffect, useRef, useState } from 'react';

export function plotColour(
  role: 'estimate' | 'reference' | 'error' | 'muted' | 'ink' | 'rule' | 'paper',
) {
  return getComputedStyle(document.documentElement).getPropertyValue(`--plot-${role}`).trim();
}

export default function Plot({
  data,
  layout,
  label,
  onTime,
}: {
  data: Data[];
  layout?: Partial<Layout>;
  label: string;
  onTime?: (time: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const renderQueue = useRef<Promise<void>>(Promise.resolve());
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    let disposed = false;
    let plot: PlotlyHTMLElement | null = null;
    const observer = new ResizeObserver(() => {
      const current = plot;
      if (current)
        void import('plotly.js-dist-min').then(({ default: Plotly }) => {
          if (!disposed) Plotly.Plots.resize(current);
        });
    });
    observer.observe(element);
    renderQueue.current = renderQueue.current
      .then(async () => {
        const { default: Plotly } = await import('plotly.js-dist-min');
        if (disposed) return;
        setError(null);
        const axis = {
          gridcolor: plotColour('rule'),
          zerolinecolor: plotColour('rule'),
          linecolor: plotColour('rule'),
          automargin: true,
        };
        plot = await Plotly.react(
          element,
          data,
          {
            paper_bgcolor: plotColour('paper'),
            plot_bgcolor: plotColour('paper'),
            font: {
              family: getComputedStyle(element).fontFamily,
              color: plotColour('ink'),
              size: 12,
            },
            margin: { l: 56, r: 20, t: 24, b: 48 },
            autosize: true,
            hovermode: 'closest',
            legend: { orientation: 'h', x: 0, y: 1.16 },
            xaxis: axis,
            yaxis: axis,
            uirevision: label,
            ...layout,
          },
          {
            responsive: true,
            displaylogo: false,
            scrollZoom: false,
            modeBarButtonsToRemove: ['sendDataToCloud'],
          },
        );
        if (disposed) return;
        plot.removeAllListeners('plotly_click');
        if (onTime)
          plot.on('plotly_click', (event: PlotMouseEvent) => {
            const time = event.points[0]?.x;
            if (typeof time === 'number') onTime(time);
          });
      })
      .catch((reason: Error) => {
        if (!disposed) setError(reason.message);
      });
    return () => {
      disposed = true;
      observer.disconnect();
    };
  }, [data, layout, label, onTime]);
  useEffect(() => {
    const element = ref.current;
    return () => {
      if (element) {
        void renderQueue.current.then(async () => {
          const { default: Plotly } = await import('plotly.js-dist-min');
          Plotly.purge(element);
        });
      }
    };
  }, []);
  return (
    <div className="plot-wrap" role="img" aria-label={label}>
      <div ref={ref} className="plot" />
      {error && (
        <p className="error" role="alert">
          Plot unavailable: {error}
        </p>
      )}
    </div>
  );
}
