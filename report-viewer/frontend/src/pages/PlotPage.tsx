import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { friendlyError, getPlot } from '../api/client';
import { HEIGHT_EXPANDED, setHeight as setIframeHeight } from '../iframeHeight';
import { ExplainSqlModal } from './searchDetail/ExplainSqlModal';
import { paginationBtn } from './searchDetail/styles';
import { chartTheme } from './chartTheme';

// A chart needs less room than a cohort table, so it starts shorter than
// HEIGHT_COMPACT and grows to the shared expanded height on request.
const HEIGHT_CHART = 400;
const CHROME = 92; // toolbar + padding + borders, subtracted to size the plot

const CONTINUOUS = new Set(['quantitative', 'temporal']);

/**
 * Bind pan/zoom to the scales, but only where it means something. Vega-Lite
 * can only bind continuous, unbinned domains, so asking for it on a bar
 * chart's category axis just logs a warning and does nothing. Scatter, line
 * and area charts over continuous axes get drag-to-pan and wheel-to-zoom;
 * everything else keeps tooltips only.
 */
function withInteractivity(spec: Record<string, unknown>) {
  // Single views only. In a layered or faceted spec a scale-bound param has
  // to live on the child unit, not the top level.
  const composite = ['layer', 'facet', 'hconcat', 'vconcat', 'concat', 'repeat'];
  if (composite.some((k) => k in spec)) return spec;
  if (!('mark' in spec)) return spec;

  const enc = spec.encoding as Record<string, { type?: string }> | undefined;
  if (!enc?.x?.type || !enc?.y?.type) return spec;
  if (!CONTINUOUS.has(enc.x.type) || !CONTINUOUS.has(enc.y.type)) return spec;

  const existing = Array.isArray(spec.params) ? spec.params : [];
  // Respect a spec that already set up its own scale binding.
  if (existing.some((p: { bind?: unknown }) => p?.bind === 'scales')) return spec;

  return {
    ...spec,
    params: [...existing, { name: 'rv_grid', select: 'interval', bind: 'scales' }],
  };
}

export default function PlotPage() {
  const { plotId = '' } = useParams<{ plotId: string }>();
  const holder = useRef<HTMLDivElement>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [sqlModalOpen, setSqlModalOpen] = useState(false);
  const [dark, setDark] = useState(
    () => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false,
  );

  const plot = useQuery({
    queryKey: ['plot', plotId],
    queryFn: () => getPlot(plotId),
    enabled: !!plotId,
  });

  useEffect(() => {
    setIframeHeight(HEIGHT_CHART);
  }, []);

  // Redraw on theme change: the config is merged at render, so an existing
  // chart follows the browser between light and dark.
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (!mq) return;
    const onChange = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  const chartHeight = (expanded ? HEIGHT_EXPANDED : HEIGHT_CHART) - CHROME;

  const spec = useMemo(() => {
    if (!plot.data) return null;
    return {
      ...withInteractivity(plot.data.spec),
      data: { values: plot.data.rows },
      // fit-x only. Plain 'fit' with width:'container' sizes the plot to the
      // full width and then hangs the legend off the side of it, which
      // overflows the frame.
      width: 'container',
      height: chartHeight,
      autosize: { type: 'fit-x', contains: 'padding' },
      config: chartTheme(dark),
    };
  }, [plot.data, chartHeight, dark]);

  useEffect(() => {
    const el = holder.current;
    if (!el || !spec) return;
    let view: { finalize: () => void } | null = null;
    let cancelled = false;
    setRenderError(null);
    // Imported here so the vega bundle is fetched only by this route.
    import('vega-embed')
      .then(({ default: embed }) =>
        embed(el, spec as never, {
          // The action menu is Save as PNG/SVG and View Source. The editor
          // link goes off-origin, which the CSP blocks anyway.
          actions: { export: true, source: true, compiled: false, editor: false },
          renderer: 'svg',
          tooltip: { theme: dark ? 'dark' : 'light' },
        }),
      )
      .then((result) => {
        if (cancelled) result.finalize();
        else view = result;
      })
      .catch((err: unknown) => setRenderError(String(err)));
    return () => {
      cancelled = true;
      view?.finalize();
    };
  }, [spec, dark]);

  const toggleSize = () => {
    const next = !expanded;
    setExpanded(next);
    setIframeHeight(next ? HEIGHT_EXPANDED : HEIGHT_CHART);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: '1 1 auto', minHeight: 0 }}>
      {plot.error && (
        <p style={{ color: 'var(--rv-danger)' }}>{friendlyError(plot.error, 'this chart')}</p>
      )}
      {renderError && (
        <p style={{ color: 'var(--rv-danger)', fontSize: '0.8rem' }}>
          This chart could not be drawn: {renderError}
        </p>
      )}
      {!plot.data && plot.isLoading && <p style={{ color: 'var(--rv-muted)' }}>Loading chart…</p>}
      <div
        ref={holder}
        style={{
          flex: '1 1 auto',
          minHeight: 0,
          padding: '0.5rem',
          background: 'var(--rv-surface)',
          border: '1px solid var(--rv-border)',
          borderRadius: 4,
        }}
      />
      <div
        style={{
          display: 'flex',
          justifyContent: 'flex-end',
          alignItems: 'center',
          gap: '0.4rem',
          padding: '0.35rem 0.1rem 0',
        }}
      >
        {(plot.data?.sql_explanation || plot.data?.sql) && (
          <button
            type="button"
            onClick={() => setSqlModalOpen(true)}
            style={paginationBtn}
            title="See what this chart shows and the underlying SQL"
          >
            Explain Chart
          </button>
        )}
        <button
          type="button"
          onClick={toggleSize}
          style={paginationBtn}
          title={expanded ? 'Shrink chart back to compact size' : 'Grow chart for more room'}
          aria-label={expanded ? 'Contract chart' : 'Expand chart'}
        >
          {expanded ? 'Shrink' : 'Expand'}
        </button>
      </div>
      {sqlModalOpen && plot.data && (
        <ExplainSqlModal
          title="What this chart shows"
          emptyText="No plain-language explanation was attached to this chart."
          explanation={plot.data.sql_explanation}
          sql={plot.data.sql}
          highlightTerms={[]}
          highlightDiagnosis={[]}
          onClose={() => setSqlModalOpen(false)}
        />
      )}
    </div>
  );
}
