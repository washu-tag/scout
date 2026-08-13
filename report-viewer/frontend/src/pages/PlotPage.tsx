import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { friendlyError, getPlot } from '../api/client';
import { setHeight as setIframeHeight } from '../iframeHeight';

const HEIGHT_CHART = 380;

export default function PlotPage() {
  const { plotId = '' } = useParams<{ plotId: string }>();
  const holder = useRef<HTMLDivElement>(null);
  const [renderError, setRenderError] = useState<string | null>(null);

  const plot = useQuery({
    queryKey: ['plot', plotId],
    queryFn: () => getPlot(plotId),
    enabled: !!plotId,
  });

  useEffect(() => {
    setIframeHeight(HEIGHT_CHART);
  }, []);

  useEffect(() => {
    const el = holder.current;
    if (!el || !plot.data) return;
    let view: { finalize: () => void } | null = null;
    let cancelled = false;
    // Imported here so the vega bundle is fetched only by this route.
    import('vega-embed')
      .then(({ default: embed }) =>
        embed(
          el,
          {
            ...plot.data.spec,
            data: { values: plot.data.rows },
            // Fixed height: 'container' renders nothing if it resolves to 0.
            width: 'container',
            height: 280,
            autosize: { type: 'fit', contains: 'padding' },
          } as never,
          { actions: false, renderer: 'canvas' },
        ),
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
  }, [plot.data]);

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
    </div>
  );
}
