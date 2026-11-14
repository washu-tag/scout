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

const LAUNCHPAD_ACK_MS = 500;

/** Ask the Scout launchpad framing this page to open `url` in place, and
 * resolve whether it did. The launchpad is at the parent domain of
 * report-viewer's own host, answers `scout:opened` for a URL one of its apps
 * owns, and stays silent otherwise. Unlike a new tab, this works from inside
 * OWUI's sandboxed chat iframe, whose popups inherit the sandbox. */
function openInLaunchpad(url: string): Promise<boolean> {
  if (window.top === window || !window.top) return Promise.resolve(false);
  const host = window.location.host.split('.').slice(1).join('.');
  if (!host) return Promise.resolve(false);
  const launchpad = `${window.location.protocol}//${host}`;
  const top = window.top;
  return new Promise((resolve) => {
    const done = (opened: boolean) => {
      window.clearTimeout(timer);
      window.removeEventListener('message', onMessage);
      resolve(opened);
    };
    const onMessage = (event: MessageEvent) => {
      if (
        event.origin === launchpad &&
        event.data?.type === 'scout:opened' &&
        event.data.href === url
      ) {
        done(true);
      }
    };
    const timer = window.setTimeout(() => done(false), LAUNCHPAD_ACK_MS);
    window.addEventListener('message', onMessage);
    top.postMessage({ type: 'scout:open', href: url }, launchpad);
  });
}

export interface OpenResultState {
  opening: boolean;
  error: string | null;
  resultLink: string | null;
  copied: boolean;
}

/** Open a result URL from any action button, reusably.
 *
 * Inside the Scout launchpad the launchpad opens it in place
 * (`openInLaunchpad`). Otherwise this attempts a real navigation via a simulated anchor click, not
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
 * does - see security-headers-sameorigin-popups in
 * ansible/roles/traefik/tasks/main.yaml for which value is required and
 * why. A destination that hasn't opted in will always fall through to
 * the copy-link affordance when embedded in OWUI's sandboxed chat iframe.
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
    if (await openInLaunchpad(url)) {
      setState({ opening: false, error: null, resultLink: null, copied: false });
      return;
    }
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

  const dismiss = useCallback(() => {
    setState((s) => ({ ...s, resultLink: null, copied: false }));
  }, []);

  return { ...state, open, copyLink, dismiss };
}
