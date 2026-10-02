import { useCallback, useState } from 'react';

function _execCommandCopy(text: string): boolean {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try {
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    document.body.removeChild(ta);
  }
}

export interface OpenResultState {
  opening: boolean;
  error: string | null;
  resultLink: string | null;
  copied: boolean;
}

/** Open a result URL from any action button, reusably.
 *
 * Always attempts a real navigation via a simulated anchor click, not
 * `window.open()` - Chrome treats a script-controlled popup more strictly
 * than a real navigation and can block it outright with
 * ERR_BLOCKED_BY_RESPONSE / "blocked from loading in a popup opened by a
 * sandboxed iframe" (relevant when report-viewer is embedded in OWUI's
 * chat). A blocked popup doesn't throw a catchable error - the browser
 * silently drops it - so there's no reliable signal to decide whether a
 * fallback is needed; always expose the link afterward so the caller can
 * render an "open this manually" affordance unconditionally.
 *
 * Whether the popup actually succeeds is controlled by the destination's
 * own Cross-Origin-Opener-Policy response header, not anything this hook
 * does. When opened from within a sandboxed iframe (OWUI's chat embed)
 * without allow-popups-to-escape-sandbox - which we don't control - the
 * destination must send exactly COOP: unsafe-none; anything else
 * (including same-origin-allow-popups, easy to assume is "relaxed
 * enough" but isn't) hits the same block per the WHATWG HTML spec (see
 * security-headers-sameorigin-popups in
 * ansible/roles/traefik/tasks/main.yaml for the full writeup). A
 * destination that hasn't opted into unsafe-none will always fall
 * through to the copy-link affordance when embedded this way.
 *
 * Copying is a separate, explicit `copyLink` call rather than something
 * `open` does automatically: `document.execCommand('copy')` only succeeds
 * within a synchronous user gesture, and callers that resolve `open()`
 * after an `await` (e.g. a backend-call action's invoke request) no
 * longer have one by the time `open` returns. `copyLink` must be invoked
 * directly from a click handler, with no `await` ahead of it in that
 * handler, so its own click supplies the gesture.
 *
 * The copy itself uses `document.execCommand('copy')`, not
 * `navigator.clipboard` - same reasoning and precedent as
 * ExplainSqlModal.tsx's SQL-copy button: `navigator.clipboard.writeText()`
 * is blocked by OWUI's artifact-iframe Permissions-Policy (no
 * `clipboard-write` delegation on the embedding `<iframe>`, which Scout
 * doesn't control - see ADR 0029), even from a real user click.
 * execCommand is deprecated but still functional and unaffected by that
 * policy.
 */
export function useOpenResult() {
  const [state, setState] = useState<OpenResultState>({
    opening: false,
    error: null,
    resultLink: null,
    copied: false,
  });

  const open = useCallback(async (url: string) => {
    setState({ opening: true, error: null, resultLink: null, copied: false });
    try {
      const link = document.createElement('a');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      setState({ opening: false, error: null, resultLink: url, copied: false });
    } catch (err) {
      setState({
        opening: false,
        error: err instanceof Error ? err.message : 'Failed to open link',
        resultLink: null,
        copied: false,
      });
    }
  }, []);

  // Call directly from a click handler - see the doc comment above.
  const copyLink = useCallback((url: string) => {
    const ok = _execCommandCopy(url);
    setState((s) => ({ ...s, copied: ok }));
    if (ok) {
      setTimeout(() => setState((s) => ({ ...s, copied: false })), 1500);
    }
  }, []);

  // Closes the result-link modal without affecting `opening`/`error`.
  const dismiss = useCallback(() => {
    setState((s) => ({ ...s, resultLink: null, copied: false }));
  }, []);

  return { ...state, open, copyLink, dismiss };
}
