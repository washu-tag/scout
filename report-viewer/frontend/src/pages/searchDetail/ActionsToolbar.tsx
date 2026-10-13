import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { friendlyError, invokeSearchAction, type ActionDescriptor } from '../../api/client';
import { useOpenResult } from '../../openResult';
import { ResultLinkModal } from './ResultLinkModal';
import { paginationBtn } from './styles';

// Explain Search and Download CSV always stay visible - only site-authored
// actions.custom entries are eligible to collapse into "More" (the
// built-ins are few and expected, so they're never worth hiding; an
// unbounded custom list would be).
const BUILTIN_ACTION_IDS = new Set(['explain-search', 'download-csv']);

/** Custom actions that don't fit the row's available width collapse into
 * a "More ▾" dropdown - same trailing-caret/floating-panel convention as
 * the page's own "Columns ▾" picker, not a new icon. Fit is measured
 * against a hidden, always-fully-rendered "prober" copy of every custom
 * button (see the aria-hidden block below), so the natural width of a
 * button currently in the menu is still known without needing to render
 * it inline first - the naive approach of measuring only what's already
 * visible can't discover that a hidden button would actually fit.
 *
 * Renders builtins/visible-customs/"More" as flat siblings (a Fragment),
 * not wrapped in its own row - so if even this floor doesn't fit the
 * parent row's budget, the parent's own flex-wrap can still place each
 * item independently instead of moving the whole set as one block. */
export function ActionsToolbar({
  searchId,
  actions,
  clientHandlers,
  visibleReportIds,
  disabled,
  availableWidth,
  gapPx,
}: {
  searchId: string;
  actions: ActionDescriptor[];
  clientHandlers: Record<string, () => void>;
  // The caller's currently client-side-filtered rows' primary_report_identifier
  // values - forwarded to backend-call actions so a filtered view and the
  // action agree on scope (see api/client.ts's invokeSearchAction).
  visibleReportIds: string[];
  // True while rows are still loading.
  disabled?: boolean;
  // Width budgeted for this component within the parent row - computed
  // from sibling widths, not this component's own rendered size.
  availableWidth: number;
  // The parent row's own computed gap, in px - this component has no row
  // element of its own to read it from now that it renders flat.
  gapPx: number;
}) {
  const { opening, error, resultLink, copied, open, copyLink, dismiss } = useOpenResult();
  const [invokingId, setInvokingId] = useState<string | null>(null);
  const [invokeError, setInvokeError] = useState<string | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);

  const builtins = actions.filter((a) => BUILTIN_ACTION_IDS.has(a.id));
  const customs = actions.filter((a) => !BUILTIN_ACTION_IDS.has(a.id));
  const customIdsKey = customs.map((a) => a.id).join(',');

  const builtinRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const proberRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const moreProberRef = useRef<HTMLButtonElement>(null);
  const moreRef = useRef<HTMLDivElement>(null);
  const [visibleCustomCount, setVisibleCustomCount] = useState(customs.length);
  const hasBuiltins = builtins.length > 0;

  useLayoutEffect(() => {
    const builtinsWidth = builtins.reduce((sum, b, i) => {
      const w = builtinRefs.current.get(b.id)?.offsetWidth ?? 0;
      return sum + w + (i > 0 ? gapPx : 0);
    }, 0);
    const boundaryGap = hasBuiltins && customs.length > 0 ? gapPx : 0;
    const available = availableWidth - builtinsWidth - boundaryGap;
    const widths = customs.map((a) => proberRefs.current.get(a.id)?.offsetWidth ?? 0);
    const totalWidth = widths.reduce((sum, w, i) => sum + w + (i > 0 ? gapPx : 0), 0);
    let fit = customs.length;
    if (totalWidth > available) {
      // Something won't fit, so "More" renders - its own width plus the gap
      // before it has to come out of the budget too.
      const moreWidth = moreProberRef.current?.offsetWidth ?? 0;
      const budget = available - moreWidth - gapPx;
      let used = 0;
      fit = 0;
      for (const width of widths) {
        const next = used + (fit > 0 ? gapPx : 0) + width;
        if (next > budget) break;
        used = next;
        fit++;
      }
    }
    // eslint-disable-next-line no-console
    console.debug('[ActionsToolbar fit]', {
      availableWidth,
      builtinsWidth,
      boundaryGap,
      available,
      widths,
      totalWidth,
      moreWidth: moreProberRef.current?.offsetWidth,
      fit,
      customCount: customs.length,
    });
    setVisibleCustomCount(fit);
  }, [customIdsKey, hasBuiltins, availableWidth, gapPx, builtins, customs]);

  // Outside-click/Escape dismiss - mirrors the column-picker's dismiss
  // effect in SearchDetailPage.tsx.
  useEffect(() => {
    if (!moreOpen) return;
    const onEvent = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent && e.key !== 'Escape') return;
      if (e instanceof MouseEvent && moreRef.current?.contains(e.target as Node)) return;
      setMoreOpen(false);
    };
    document.addEventListener('mousedown', onEvent);
    document.addEventListener('keydown', onEvent);
    return () => {
      document.removeEventListener('mousedown', onEvent);
      document.removeEventListener('keydown', onEvent);
    };
  }, [moreOpen]);

  const handleBackendCall = async (action: ActionDescriptor) => {
    setInvokingId(action.id);
    setInvokeError(null);
    try {
      const result = await invokeSearchAction(searchId, action.id, visibleReportIds);
      await open(result.url);
    } catch (err) {
      setInvokeError(friendlyError(err, `running "${action.title}"`));
    } finally {
      setInvokingId(null);
    }
  };

  const renderAction = (
    action: ActionDescriptor,
    closeMenuOnClick: boolean,
    ref?: (el: HTMLButtonElement | null) => void,
  ) => {
    if (action.action_type === 'client') {
      const handler = action.client_handler ? clientHandlers[action.client_handler] : undefined;
      if (!handler) {
        console.warn(`ActionsToolbar: no client handler registered for action "${action.id}"`);
        return null;
      }
      return (
        <button
          key={action.id}
          ref={ref}
          type="button"
          disabled={disabled}
          onClick={() => {
            handler();
            if (closeMenuOnClick) setMoreOpen(false);
          }}
          className="scout-toolbar-btn"
          style={{ ...paginationBtn, flexShrink: 0 }}
          title={action.title}
        >
          {action.title}
        </button>
      );
    }
    if (action.action_type === 'backend-call') {
      return (
        <button
          key={action.id}
          ref={ref}
          type="button"
          disabled={disabled || invokingId === action.id || opening}
          onClick={() => {
            void handleBackendCall(action);
            if (closeMenuOnClick) setMoreOpen(false);
          }}
          className="scout-toolbar-btn"
          style={{ ...paginationBtn, flexShrink: 0 }}
          title={action.title}
        >
          {invokingId === action.id ? `${action.title}…` : action.title}
        </button>
      );
    }
    return (
      <button
        key={action.id}
        ref={ref}
        type="button"
        disabled={disabled || opening}
        onClick={() => {
          if (action.url) open(action.url);
          if (closeMenuOnClick) setMoreOpen(false);
        }}
        className="scout-toolbar-btn"
        style={{ ...paginationBtn, flexShrink: 0 }}
        title={action.title}
      >
        {action.title}
      </button>
    );
  };

  const visibleCustoms = customs.slice(0, visibleCustomCount);
  const overflowCustoms = customs.slice(visibleCustomCount);

  return (
    <>
      {builtins.map((a) =>
        renderAction(a, false, (el) => {
          if (el) builtinRefs.current.set(a.id, el);
          else builtinRefs.current.delete(a.id);
        }),
      )}
      {visibleCustoms.map((a) => renderAction(a, false))}
      {overflowCustoms.length > 0 && (
        <div ref={moreRef} style={{ position: 'relative', flexShrink: 0 }}>
          <button
            type="button"
            onClick={() => setMoreOpen((v) => !v)}
            className="scout-toolbar-btn"
            style={paginationBtn}
            title="More actions"
          >
            More ▾
          </button>
          {moreOpen && (
            <div
              style={{
                position: 'absolute',
                // Opens upward, like "Columns ▾" - this row can sit at
                // the bottom of a height-constrained iframe.
                bottom: '100%',
                right: 0,
                marginBottom: 4,
                background: 'var(--rv-surface)',
                border: '1px solid var(--rv-border)',
                borderRadius: 4,
                boxShadow: '0 4px 12px rgba(0,0,0,0.12)',
                padding: '0.3rem',
                zIndex: 10,
                minWidth: 140,
                display: 'flex',
                flexDirection: 'column',
                gap: '0.25rem',
              }}
            >
              {overflowCustoms.map((a) => renderAction(a, true))}
            </div>
          )}
        </div>
      )}
      {/* Hidden, always-fully-rendered probers - see the doc comment above.
          Anchored to the parent toolbar row, which is position: relative. */}
      <div
        aria-hidden="true"
        style={{
          position: 'absolute',
          visibility: 'hidden',
          pointerEvents: 'none',
          height: 0,
          overflow: 'hidden',
          display: 'flex',
          gap: '0.5rem',
        }}
      >
        {customs.map((a) => (
          <button
            key={a.id}
            type="button"
            tabIndex={-1}
            ref={(el) => {
              if (el) proberRefs.current.set(a.id, el);
              else proberRefs.current.delete(a.id);
            }}
            style={paginationBtn}
          >
            {a.title}
          </button>
        ))}
        <button ref={moreProberRef} type="button" tabIndex={-1} style={paginationBtn}>
          More ▾
        </button>
      </div>
      {(error || invokeError) && (
        <p style={{ color: 'var(--rv-danger)', margin: 0, flexBasis: '100%' }}>
          {error || invokeError}
        </p>
      )}
      {resultLink && (
        <ResultLinkModal
          url={resultLink}
          copied={copied}
          onCopy={() => copyLink(resultLink)}
          onClose={dismiss}
        />
      )}
    </>
  );
}
