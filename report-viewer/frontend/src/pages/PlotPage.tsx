import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
const BAND = 22; // pixels per category on a discrete axis
const CONTINUOUS_HEIGHT = 300; // scatter/line: nothing to count, so pick one

const CONTINUOUS = new Set(['quantitative', 'temporal']);

type Enc = Record<string, { type?: string; field?: string } | undefined>;

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

// Click-to-isolate on the legend, injected for the same reason as
// `withInteractivity`. Recurses into a facet's child spec, where color lives.
function withLegendSelection(spec: Record<string, unknown>): Record<string, unknown> {
  const child = (spec as { spec?: unknown }).spec;
  if (isComposite(spec)) {
    return child && typeof child === 'object'
      ? { ...spec, spec: withLegendSelection(child as Record<string, unknown>) }
      : spec;
  }
  if (!('mark' in spec)) return spec;

  const enc = spec.encoding as Enc | undefined;
  const color = enc?.color;
  if (!color?.type || color.type === 'quantitative' || typeof color.field !== 'string') return spec;
  if ((enc as Record<string, unknown>).opacity) return spec;

  const existing = Array.isArray(spec.params) ? spec.params : [];
  if (existing.some((p: { bind?: unknown }) => p?.bind === 'legend')) return spec;

  return {
    ...spec,
    params: [
      ...existing,
      { name: 'rv_legend', select: { type: 'point', fields: [color.field] }, bind: 'legend' },
    ],
    encoding: { ...enc, opacity: { condition: { param: 'rv_legend', value: 1 }, value: 0.2 } },
  };
}

// Wrapping only takes effect from a top-level `columns`, but the model
// consistently nests it inside the facet channel instead - hoist it out.
function hoistFacetColumns(spec: Record<string, unknown>) {
  const facet = spec.facet;
  if (!facet || typeof facet !== 'object' || typeof spec.columns === 'number') return spec;
  const { columns, ...rest } = facet as Record<string, unknown>;
  return typeof columns === 'number' ? { ...spec, facet: rest, columns } : spec;
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
  // Vega-Lite doesn't support `fit` autosize on facet/concat.
  if (isComposite(spec)) return { height: CONTINUOUS_HEIGHT };

  const enc = spec.encoding as Enc | undefined;
  const yType = enc?.y?.type;
  const stepped = !!yType && !CONTINUOUS.has(yType);
  return stepped
    ? { height: { step: BAND }, autosize: { type: 'fit-x', contains: 'padding' } }
    : { height: CONTINUOUS_HEIGHT, autosize: { type: 'fit', contains: 'padding' } };
}

const FACET_GUTTER = 20; // Vega-Lite's default spacing between facet panels
const HOLDER_PADDING = 16; // holder's own 0.5rem left+right padding
const AXIS_RESERVE = 71; // first column's y axis width (labels + title + ticks); tune by testing
const MIN_PANEL_WIDTH = 120; // a panel narrower than this is unreadable

// Only an explicit wrap count gets clamped/rewritten below.
function explicitFacetColumns(spec: Record<string, unknown>): number | undefined {
  if (typeof spec.columns === 'number') return spec.columns;
  const facet = spec.facet as { columns?: unknown } | undefined;
  return typeof facet?.columns === 'number' ? facet.columns : undefined;
}

// Vega-Lite only resizes `container` width responsively for a single view or
// layer, so a facet's child spec needs an explicit pixel width instead.
function withContainerWidth(spec: Record<string, unknown>, containerWidth: number) {
  const child = (spec as { spec?: unknown }).spec;
  if (!isComposite(spec) || !child || typeof child !== 'object') {
    return { ...spec, width: 'container' };
  }

  const requested = explicitFacetColumns(spec);
  const available = Math.max(0, containerWidth - HOLDER_PADDING - AXIS_RESERVE);
  let columns = requested ?? 1;
  let patch: Record<string, unknown> = {};
  if (containerWidth > 0 && requested !== undefined) {
    const maxColumns = Math.max(1, Math.floor(available / (MIN_PANEL_WIDTH + FACET_GUTTER)));
    columns = Math.min(requested, maxColumns);
    patch = { columns };
  }

  const width =
    containerWidth > 0
      ? Math.max(MIN_PANEL_WIDTH, Math.floor((available - (columns - 1) * FACET_GUTTER) / columns))
      : 'container';
  return { ...spec, ...patch, spec: { ...(child as Record<string, unknown>), width } };
}

export default function PlotPage() {
  const { plotId = '' } = useParams<{ plotId: string }>();
  const requestPrompt = useChatPrompt();
  const content = useRef<HTMLDivElement>(null);
  const holder = useRef<HTMLDivElement>(null);
  const naturalHeight = useRef(MIN_HEIGHT);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [sqlModalOpen, setSqlModalOpen] = useState(false);
  const [containerWidth, setContainerWidth] = useState(0);
  const [dark, setDark] = useState(
    () => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false,
  );

  const plot = useQuery({
    queryKey: ['plot', plotId],
    queryFn: () => getPlot(plotId),
    enabled: !!plotId,
  });

  const base = useMemo(
    () =>
      plot.data ? withLegendSelection(withInteractivity(hoistFacetColumns(plot.data.spec))) : null,
    [plot.data],
  );
  // Only a facet needs the measured width - a single view already resizes
  // itself via `container`, so this stays stable and skips its re-embeds.
  const isFacet = !!base && isComposite(base);

  // Redraw on theme change: the config is merged at render, so an existing
  // chart follows the browser between light and dark.
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (!mq) return;
    const onChange = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  // Measure `content`, which is content-sized, not the scroller around it - the
  // scroller is capped at the frame viewport and would just report the height
  // it already has. `top` is the shell's padding above the content, doubled to
  // cover the matching strip below the buttons.
  const measure = useCallback(() => {
    const el = content.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    naturalHeight.current = Math.max(
      MIN_HEIGHT,
      Math.ceil(rect.height + Math.max(0, rect.top) * 2),
    );
    setIframeHeight(naturalHeight.current);
    if (isFacet) {
      setContainerWidth((prev) => (Math.abs(prev - rect.width) > 4 ? rect.width : prev));
    }
  }, [isFacet]);

  // The chart is width-fitted, so a width change redraws it at a new height.
  useEffect(() => {
    const el = content.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure]);

  const spec = useMemo(() => {
    if (!plot.data || !base) return null;
    return {
      ...withContainerWidth(base, containerWidth),
      data: { values: plot.data.rows },
      ...sizing(base),
      config: chartTheme(dark),
    };
  }, [plot.data, base, containerWidth, dark]);

  useEffect(() => {
    const el = holder.current;
    if (!el || !spec) return;
    let view: { finalize: () => void } | null = null;
    let cancelled = false;
    setRenderError(null);
    // Imported here so the vega bundle is fetched only by this route.
    Promise.all([import('vega-embed'), import('vega-interpreter')])
      .then(([{ default: embed }, { expressionInterpreter }]) =>
        embed(el, spec as never, {
          // CSP-safe mode: skips Vega's eval-based codegen so a model-authored spec cannot execute JS.
          ast: true,
          expr: expressionInterpreter,
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
        measure();
      })
      .catch((err: unknown) => setRenderError(String(err)));
    return () => {
      cancelled = true;
      view?.finalize();
    };
  }, [spec, dark, measure]);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        flex: '1 1 auto',
        minHeight: 0,
        overflowY: 'auto',
      }}
    >
      <div ref={content} style={{ display: 'flex', flexDirection: 'column', flex: '0 0 auto' }}>
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
            // Uncapped: height comes from the drawing, and the frame follows.
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
                  description:
                    "Pull this chart's data into the chat and get the model's read on it.",
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
      </div>
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
