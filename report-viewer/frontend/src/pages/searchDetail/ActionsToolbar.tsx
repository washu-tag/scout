import { useState } from 'react';
import { friendlyError, invokeSearchAction, type ActionDescriptor } from '../../api/client';
import { useOpenResult } from '../../openResult';
import { paginationBtn } from './styles';

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
 * 0034's "bad chip costs the chip" grading for launchpad tiles. */
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
  const { opening, error, copiedLink, copyFailed, open } = useOpenResult();
  const [invokingId, setInvokingId] = useState<string | null>(null);
  const [invokeError, setInvokeError] = useState<string | null>(null);

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

  return (
    <>
      {actions.map((action) => {
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
              onClick={handler}
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
              onClick={() => handleBackendCall(action)}
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
            onClick={() => action.url && open(action.url)}
            style={paginationBtn}
            title={action.title}
          >
            {action.title}
          </button>
        );
      })}
      {(error || invokeError) && (
        <p style={{ color: 'var(--rv-danger)', margin: '0.25rem 0 0' }}>{error || invokeError}</p>
      )}
      {copiedLink && (
        <p style={{ margin: '0.25rem 0 0' }}>
          A new tab should have opened.{' '}
          {copyFailed
            ? "Couldn't copy the link automatically, though"
            : 'Its link is also copied to your clipboard'}
          {", in case it didn't: "}
          <code>{copiedLink}</code>
        </p>
      )}
    </>
  );
}
