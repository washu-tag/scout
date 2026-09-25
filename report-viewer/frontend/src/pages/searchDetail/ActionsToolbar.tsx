import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { friendlyError, invokeSearchAction, type ActionDescriptor } from '../../api/client';
import { useOpenResult } from '../../openResult';
import { ResultLinkModal } from './ResultLinkModal';
import { paginationBtn } from './styles';

// Explain Search and Download CSV always stay visible - only site-authored
// actions.custom entries are eligible to collapse into "More" (#739 demo
// feedback: an unbounded custom list wrapping the whole pagination row to
// a second line looked broken; the built-ins are few and expected, so
// they're never worth hiding).
const BUILTIN_ACTION_IDS = new Set(['explain-search', 'download-csv']);

// Matches the row's own 0.5rem gap at the default 16px root font size -
// used by the fit calculation below, which works in raw pixels.
const GAP_PX = 8;

/** Issue #739: renders a backend-declared, group-filtered action list
 * generically.
 * - `open-url` actions are handled uniformly via `useOpenResult`.
 * - `client` actions are dispatched to a handler the page registers by
 *   id - report-viewer can't ship page-specific logic (e.g. building a
 *   CSV from the currently loaded/filtered rows) through a backend
 *   descriptor.
 * - `backend-call` actions POST to the action's own invoke route to get
 *   a dynamically-computed result URL from a genuinely separate service
 *   (report-viewer never needs to know what that service does), then
 *   get the same open/copy-fallback handling as open-url.
 *
 * An action naming an unregistered client handler is skipped with a
 * console diagnostic rather than breaking the toolbar - mirrors ADR
 * 0034's "bad chip costs the chip" grading for launchpad tiles.
 *
 * Custom actions that don't fit the row's available width collapse into
 * a "More ▾" dropdown - same trailing-caret/floating-panel convention as
 * the page's own "Columns ▾" picker, not a new icon. Fit is measured
 * against a hidden, always-fully-rendered "prober" copy of every custom
 * button (see the aria-hidden block below), so the natural width of a
 * button currently in the menu is still known without needing to render
 * it inline first - the naive approach of measuring only what's already
 * visible can't discover that a hidden button would actually fit. */
export function ActionsToolbar({
  searchId,
  actions,
  clientHandlers,
  visibleReportIds,
}: {
  searchId: string;
  actions: ActionDescriptor[];
  clientHandlers: Record<string, () => void>;
  // The caller's currently client-side-filtered rows' primary_report_identifier
  // values - forwarded to backend-call actions so a filtered view and the
  // action agree on scope (see api/client.ts's invokeSearchAction).
  visibleReportIds: string[];
}) {
  const { opening, error, resultLink, copied, open, copyLink, dismiss } = useOpenResult();
  const [invokingId, setInvokingId] = useState<string | null>(null);
  const [invokeError, setInvokeError] = useState<string | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);

  const builtins = actions.filter((a) => BUILTIN_ACTION_IDS.has(a.id));
  const customs = actions.filter((a) => !BUILTIN_ACTION_IDS.has(a.id));
  const customIdsKey = customs.map((a) => a.id).join(',');

  const rowRef = useRef<HTMLDivElement>(null);
  const proberRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const moreProberRef = useRef<HTMLButtonElement>(null);
  const moreRef = useRef<HTMLDivElement>(null);
  const [visibleCustomCount, setVisibleCustomCount] = useState(customs.length);

  // Recomputes how many customs fit whenever the row is resized (window
  // resize, iframe expand/contract, sidebar toggle, ...) or the action
  // list itself changes.
  useLayoutEffect(() => {
    const row = rowRef.current;
    if (!row) return;
    const recompute = () => {
      const available = row.clientWidth;
      const moreWidth = moreProberRef.current?.offsetWidth ?? 0;
      let used = 0;
      let fit = 0;
      for (let i = 0; i < customs.length; i++) {
        const width = proberRefs.current.get(customs[i].id)?.offsetWidth ?? 0;
        const hasMoreAfter = i + 1 < customs.length;
        // Reserve room for the "More" button unless this is the last
        // custom action - no point reserving space for a dropdown that
        // would end up empty.
        const reserve = hasMoreAfter ? moreWidth + GAP_PX : 0;
        const next = used + (fit > 0 ? GAP_PX : 0) + width;
        if (next + reserve > available) break;
        used = next;
        fit++;
      }
      setVisibleCustomCount(fit);
    };
    recompute();
    const ro = new ResizeObserver(recompute);
    ro.observe(row);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customIdsKey]);

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

  const renderAction = (action: ActionDescriptor, closeMenuOnClick: boolean) => {
    if (action.action_type === 'client') {
      const handler = action.client_handler ? clientHandlers[action.client_handler] : undefined;
      if (!handler) {
        console.warn(`ActionsToolbar: no client handler registered for action "${action.id}"`);
        return null;
      }
      return (
        <button
          key={action.id}
          type="button"
          onClick={() => {
            handler();
            if (closeMenuOnClick) setMoreOpen(false);
          }}
          style={paginationBtn}
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
          type="button"
          disabled={invokingId === action.id || opening}
          onClick={() => {
            void handleBackendCall(action);
            if (closeMenuOnClick) setMoreOpen(false);
          }}
          style={paginationBtn}
          title={action.title}
        >
          {invokingId === action.id ? `${action.title}…` : action.title}
        </button>
      );
    }
    return (
      <button
        key={action.id}
        type="button"
        disabled={opening}
        onClick={() => {
          if (action.url) open(action.url);
          if (closeMenuOnClick) setMoreOpen(false);
        }}
        style={paginationBtn}
        title={action.title}
      >
        {action.title}
      </button>
    );
  };

  const visibleCustoms = customs.slice(0, visibleCustomCount);
  const overflowCustoms = customs.slice(visibleCustomCount);

  return (
    <div
      style={{ flex: '1 1 auto', minWidth: 0, display: 'flex', flexDirection: 'column', gap: '0.25rem' }}
    >
      <div
        ref={rowRef}
        style={{
          display: 'flex',
          flexWrap: 'nowrap',
          overflow: 'hidden',
          gap: '0.5rem',
          alignItems: 'center',
          minWidth: 0,
        }}
      >
        {builtins.map((a) => renderAction(a, false))}
        {visibleCustoms.map((a) => renderAction(a, false))}
        {overflowCustoms.length > 0 && (
          <div ref={moreRef} style={{ position: 'relative', flexShrink: 0 }}>
            <button
              type="button"
              onClick={() => setMoreOpen((v) => !v)}
              style={paginationBtn}
              title="More actions"
            >
              More ▾
            </button>
            {moreOpen && (
              <div
                style={{
                  position: 'absolute',
                  top: '100%',
                  right: 0,
                  marginTop: 4,
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
      </div>
      {/* Hidden, always-fully-rendered probers - see the doc comment above. */}
      <div
        aria-hidden="true"
        style={{
          // height: 0 + overflow: hidden (not position: absolute) collapses
          // this to zero visible/layout height while still laying out its
          // children horizontally at their natural width, with no
          // dependency on some ancestor's positioning context.
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
        <p style={{ color: 'var(--rv-danger)', margin: 0 }}>{error || invokeError}</p>
      )}
      {resultLink && (
        <ResultLinkModal
          url={resultLink}
          copied={copied}
          onCopy={() => copyLink(resultLink)}
          onClose={dismiss}
        />
      )}
    </div>
  );
}
