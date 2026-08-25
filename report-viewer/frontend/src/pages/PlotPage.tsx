import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { friendlyError, getPlot } from '../api/client';
import { setHeight as setIframeHeight } from '../iframeHeight';
import { buildDiscussPlotPrompt } from '../chat';
import { useChatPrompt } from '../ChatPrompt';
import { ExplainSqlModal } from './searchDetail/ExplainSqlModal';
import { paginationBtn } from './searchDetail/styles';
import { chartTheme } from './chartTheme';

// The chart sizes itself to its content and the iframe follows, rather than
// the content being squeezed into a fixed box. Two bars should not get the
// same height as twenty diagnoses.
const MIN_HEIGHT = 320; // a two-bar chart should not be a sliver
// The explain panel is fixed to the iframe viewport at 80vh, so a short chart
// would give it a letterbox. Grow the frame while it is open instead of
// padding every small chart to suit a panel that is usually closed.
const MODAL_HEIGHT = 520;
const BAND = 22; // pixels per category on a discrete axis
const CONTINUOUS_HEIGHT = 300; // scatter/line: nothing to count, so pick one

const CONTINUOUS = new Set(['quantitative', 'temporal']);

type Enc = Record<string, { type?: string } | undefined>;

const COMPOSITE = ['layer', 'facet', 'hconcat', 'vconcat', 'concat', 'repeat'];

function isComposite(spec: Record<string, unknown>) {
  return COMPOSITE.some((k) => k in spec);
}

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
  if (isComposite(spec) || !('mark' in spec)) return spec;

  const enc = spec.encoding as Enc | undefined;
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

/**
 * Height and autosize for one spec.
 *
 * A discrete y axis gets `step` sizing, one band per category, so ten
 * modalities draw taller than three and nothing is crushed. Step sizing is
 * content-driven, so height cannot also be `fit` - only the width is fitted to
 * the container. Everything else takes a fixed height with a full `fit`, which
 * keeps axes and titles inside the box instead of spilling past it.
 */
function sizing(spec: Record<string, unknown>) {
  const enc = spec.encoding as Enc | undefined;
  const yType = enc?.y?.type;
  const stepped = !isComposite(spec) && !!yType && !CONTINUOUS.has(yType);
  return stepped
    ? { height: { step: BAND }, autosize: { type: 'fit-x', contains: 'padding' } }
    : { height: CONTINUOUS_HEIGHT, autosize: { type: 'fit', contains: 'padding' } };
}

export default function PlotPage() {
  const { plotId = '' } = useParams<{ plotId: string }>();
  const requestPrompt = useChatPrompt();
  const page = useRef<HTMLDivElement>(null);
  const holder = useRef<HTMLDivElement>(null);
  // What the chart itself asked for, so the frame can be restored after the
  // explain panel closes.
  const naturalHeight = useRef(MIN_HEIGHT);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [sqlModalOpen, setSqlModalOpen] = useState(false);
  const [dark, setDark] = useState(
    () => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false,
  );

  const plot = useQuery({
    queryKey: ['plot', plotId],
    queryFn: () => getPlot(plotId),
    enabled: !!plotId,
  });

  // Redraw on theme change: the config is merged at render, so an existing
  // chart follows the browser between light and dark.
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (!mq) return;
    const onChange = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  // Give the explain panel room while it is open, then hand the frame back to
  // the chart. Kept out of the render effect so opening it does not redraw.
  useEffect(() => {
    setIframeHeight(
      sqlModalOpen ? Math.max(naturalHeight.current, MODAL_HEIGHT) : naturalHeight.current,
    );
  }, [sqlModalOpen]);

  const spec = useMemo(() => {
    if (!plot.data) return null;
    const base = withInteractivity(plot.data.spec);
    return {
      ...base,
      data: { values: plot.data.rows },
      width: 'container',
      ...sizing(base),
      config: chartTheme(dark),
    };
  }, [plot.data, dark]);

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
          // Export only. `source` opens a blank window because OWUI's iframe
          // sandbox blocks writing to it, and `editor` is off-origin so the
          // CSP would block it too.
          actions: { export: true, source: false, compiled: false, editor: false },
          renderer: 'svg',
          tooltip: { theme: dark ? 'dark' : 'light' },
        }),
      )
      .then((result) => {
        if (cancelled) {
          result.finalize();
          return;
        }
        view = result;
        // The drawing is done, so let the iframe take its actual size rather
        // than the chart being squeezed into a guess. Measure the whole page,
        // not just the chart, so the id row and button row are never chopped
        // off no matter what surrounds the chart.
        const drawn = (page.current ?? el).getBoundingClientRect().height;
        naturalHeight.current = Math.max(MIN_HEIGHT, Math.ceil(drawn));
        setIframeHeight(naturalHeight.current);
      })
      .catch((err: unknown) => setRenderError(String(err)));
    return () => {
      cancelled = true;
      view?.finalize();
    };
  }, [spec, dark]);

  return (
    <div
      ref={page}
      style={{ display: 'flex', flexDirection: 'column', flex: '1 1 auto', minHeight: 0 }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          marginBottom: '0.3rem',
          fontSize: '0.85rem',
          flex: '0 0 auto',
        }}
      >
        <span style={{ flex: 1 }} />
        {plot.data && (
          <span
            title="Chart ID"
            style={{
              color: 'var(--rv-muted)',
              fontSize: '0.7rem',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              userSelect: 'all',
            }}
          >
            {plotId}
          </span>
        )}
      </div>
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
          // Height comes from the drawing, not the other way round. No cap and
          // no overflow: a scrollbar inside an iframe is a trap, so the frame
          // grows instead and the user scrolls the chat like any other page.
          padding: '0.5rem',
          background: 'var(--rv-surface)',
          border: '1px solid var(--rv-border)',
          borderRadius: 4,
        }}
      />
      {(plot.data?.sql_explanation || plot.data?.sql) && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: '0.5rem',
            padding: '0.3rem 0.1rem 0',
          }}
        >
          <button
            type="button"
            onClick={() =>
              requestPrompt(buildDiscussPlotPrompt(plotId), {
                title: 'Discuss in Chat?',
                description: "Pull this chart's data into the chat and get the model's read on it.",
              })
            }
            style={paginationBtn}
            title="Pull this chart's data into the chat and get the model's read on it"
          >
            Discuss in Chat
          </button>
          <button
            type="button"
            onClick={() => setSqlModalOpen(true)}
            style={paginationBtn}
            title="See what this search matches and the underlying SQL"
          >
            Explain Search
          </button>
        </div>
      )}
      {sqlModalOpen && plot.data && (
        <ExplainSqlModal
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
