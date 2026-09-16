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
  copiedLink: string | null;
  copyFailed: boolean;
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
 * fallback is needed; always also copy the link and expose it, so the
 * caller can render an "in case that didn't open" affordance
 * unconditionally.
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
    copiedLink: null,
    copyFailed: false,
  });

  const open = useCallback(async (url: string) => {
    setState({ opening: true, error: null, copiedLink: null, copyFailed: false });
    try {
      const link = document.createElement('a');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      const copyFailed = !_execCommandCopy(url);
      setState({ opening: false, error: null, copiedLink: url, copyFailed });
    } catch (err) {
      setState({
        opening: false,
        error: err instanceof Error ? err.message : 'Failed to open link',
        copiedLink: null,
        copyFailed: false,
      });
    }
  }, []);

  return { ...state, open };
}
